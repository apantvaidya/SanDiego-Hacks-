"""Gemini ranks candidates and returns IDs + short reasons only. Code drops unknown IDs. Owned by #7.

Rules enforced in code, not trusted to the model:
  * only IDs from the candidate set, no duplicates, at most MAX_OPTIONS
  * reasons carry no facts: any reason with a price, digit, or time is replaced by a
    fixed reason for that kind of place (prices and hours come from Neon in render)
  * uninsured: when the candidates include both a sliding-fee clinic and an urgent care,
    the result shows both, so the user sees the tradeoff (HANDOFF section 3)
  * Gemini down: rank by distance with fixed reasons
"""
from __future__ import annotations

import json
import logging
import re

from app.llm.gemini import GeminiError, generate_json
from app.models import CareLevel, CoverageType, Location, Profile, RankedOption

log = logging.getLogger(__name__)

MAX_OPTIONS = 3
MAX_REASON_CHARS = 90

DEFAULT_REASON = {
    "en": {
        "kaiser": "In your Kaiser network",
        "fqhc": "Community clinic with fees based on income",
        "urgent_care": "Urgent care that takes walk-ins",
        "er": "Emergency department, open around the clock",
        "telehealth": "Visit by phone or video",
        "lab": "Lab",
    },
    "es": {
        "kaiser": "En tu red de Kaiser",
        "fqhc": "Clínica comunitaria con tarifas según tus ingresos",
        "urgent_care": "Atención urgente sin cita",
        "er": "Sala de emergencias, abierta todo el día",
        "telehealth": "Consulta por teléfono o video",
        "lab": "Laboratorio",
    },
}

RANK_SYSTEM = """\
You rank nearby places for someone who texted a health concern. You only choose and order
place ids; another system shows every fact (name, price, hours, address).

Return up to 3 ids from the candidate list, best first, each with a short reason
(under 12 words) in the requested language. Reasons must NOT contain prices, dollar amounts,
numbers, hours, times, or addresses. Say why it fits, for example "Has X-ray for a possible
sprain" or "Lowest cost for your income". Do not name a diagnosis. In Spanish, address the
person as tú (informal), never usted.

Prefer, in order: can handle the concern (X-ray when an injury may need imaging), fits the
person's coverage and budget, then distance. For someone uninsured with low income, when
both exist, include a sliding-fee community clinic AND an urgent care with X-ray so they can
compare. Never invent ids."""

RANK_SCHEMA = {
    "type": "object",
    "properties": {
        "ranked": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["id", "reason"],
            },
        }
    },
    "required": ["ranked"],
}

_FACTY = re.compile(r"[$\d]|\b(am|pm|a\.m\.|p\.m\.)\b", re.IGNORECASE)


def _lang(profile: Profile) -> str:
    return "es" if (profile.language or "en").lower().startswith("es") else "en"


def _coverage(profile: Profile) -> str:
    ct = profile.coverage_type
    return ct.value if isinstance(ct, CoverageType) else str(ct)


def _cost_basis(loc: Location, profile: Profile) -> str:
    cov = _coverage(profile)
    if cov == "medi_cal":
        return "covered by their Medi-Cal plan"
    if cov == "kaiser":
        return "in their Kaiser network"
    if cov == "uninsured":
        if loc.sliding_scale is not None or loc.kind == "fqhc":
            return "sliding fee by income"
        if loc.self_pay_price is not None:
            return "cash price listed"
        if loc.kind == "er":
            return "ER bill, hospital charity care may apply"
        return "cash price not listed"
    return "in network"


def _compact(c: Location, profile: Profile) -> dict:
    return {
        "id": c.id, "kind": c.kind, "miles": c.distance_miles,
        "has_xray": c.has_xray, "walk_in": c.walk_in, "cost_basis": _cost_basis(c, profile),
    }


def _default_reason(loc: Location, lang: str) -> str:
    return DEFAULT_REASON[lang].get(loc.kind, DEFAULT_REASON[lang]["urgent_care"])


def _clean_reason(reason: str, loc: Location, lang: str) -> str:
    reason = " ".join(str(reason or "").split())
    if not reason or _FACTY.search(reason):
        return _default_reason(loc, lang)
    if len(reason) > MAX_REASON_CHARS:
        reason = reason[:MAX_REASON_CHARS].rsplit(" ", 1)[0]
    return reason


def _by_distance(candidates: list[Location]) -> list[Location]:
    return sorted(candidates, key=lambda c: (c.distance_miles is None, c.distance_miles or 0))


def _ensure_tradeoff(chosen: list[RankedOption], candidates: list[Location], profile: Profile,
                     lang: str) -> list[RankedOption]:
    """Uninsured: if both a sliding-fee clinic and an urgent care exist, show both."""
    if _coverage(profile) != "uninsured":
        return chosen
    by_id = {c.id: c for c in candidates}
    for kind in ("fqhc", "urgent_care"):
        if any(by_id[o.location_id].kind == kind for o in chosen):
            continue
        pool = [c for c in _by_distance(candidates) if c.kind == kind]
        if not pool:
            continue
        add = RankedOption(location_id=pool[0].id, reason=_default_reason(pool[0], lang))
        if len(chosen) < MAX_OPTIONS:
            chosen.append(add)
            continue
        # Full: replace the last option whose kind is not needed for the tradeoff or repeats.
        kinds = [by_id[o.location_id].kind for o in chosen]
        for i in range(len(chosen) - 1, -1, -1):
            k = kinds[i]
            if k not in ("fqhc", "urgent_care") or kinds.count(k) > 1:
                chosen[i] = add
                break
    return chosen


def rank(candidates: list[Location], profile: Profile, symptom_text: str,
         level: CareLevel) -> list[RankedOption]:
    if not candidates:
        return []
    lang = _lang(profile)
    by_id = {c.id: c for c in candidates}
    prompt = json.dumps({
        "concern": symptom_text,
        "level_of_care": level.value,
        "coverage": _coverage(profile),
        "income_vs_poverty_line_pct": getattr(profile.income_range_fpl, "value", profile.income_range_fpl),
        "reason_language": "Spanish" if lang == "es" else "English",
        "candidates": [_compact(c, profile) for c in candidates],
    }, ensure_ascii=False)

    try:
        out = generate_json(prompt=prompt, schema=RANK_SCHEMA, system=RANK_SYSTEM)
        ranked = out.get("ranked") or []
    except GeminiError:
        log.exception("rank: Gemini failed, ranking by distance")
        ranked = []

    chosen: list[RankedOption] = []
    seen: set[str] = set()
    for item in ranked:
        if not isinstance(item, dict):
            continue
        loc_id = item.get("id")
        if loc_id not in by_id or loc_id in seen:
            if loc_id not in by_id:
                log.warning("rank: dropping id not in candidates: %r", loc_id)
            continue
        seen.add(loc_id)
        chosen.append(RankedOption(location_id=loc_id, reason=_clean_reason(item.get("reason"), by_id[loc_id], lang)))
        if len(chosen) == MAX_OPTIONS:
            break

    if not chosen:  # Gemini down or returned nothing usable
        chosen = [RankedOption(location_id=c.id, reason=_default_reason(c, lang))
                  for c in _by_distance(candidates)[:MAX_OPTIONS]]

    return _ensure_tradeoff(chosen, candidates, profile, lang)
