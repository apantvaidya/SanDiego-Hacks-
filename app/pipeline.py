"""End-to-end question flow. Owned by #17.

first_reply(msg, profile): safety already passed (router, #3).
    Text 1 = triage.first_aid_reply. In parallel, level_of_care runs and is saved in context,
    so Text 2 does not wait for it. State -> awaiting_location (or idle on a red flag).

options_reply(msg, profile, symptom_text):
    extract_place + geocode (None: ask for a ZIP, stay in awaiting_location)
    -> level from context (or level_of_care now) + bump_if_unsure
    -> find_candidates_widening (5 mi, then 15 mi once)
    -> empty: render_fallback
    -> rank -> estimate_cost each -> verify_live on #1 (VERIFY_BUDGET_S, else skipped)
    -> render_options.
    Then save last_level_of_care + recommended_location_ids (all the follow-up needs),
    schedule the follow-up, state -> idle.

after_answer(msg, profile, context): the next message after Text 2 (router, while the answer is
    recent). One Gemini call sorts it: "directions" gets map links built in code from the
    database; "update" (more about the same problem, like "it might be broken") gets fresh
    first aid plus options for the saved location, without asking where they are again; "other"
    (thanks, ok) gets a short reply; "new" returns None and the router starts a new question.

Latency: Text 2 must arrive in about 10 s. Measured parts: sd_places hit about 0 s
(Gemini place extraction about 1 s otherwise), candidate query under 0.5 s, rank about 1 s.
verify_live gets at most VERIFY_BUDGET_S; when it is slower the card says "checked <crawl
time>" instead of "verified", which is the honest label.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from app.care.cost import estimate_cost
from app.care.find_care import find_candidates_widening
from app.care.rank import rank
from app.care.render import render_fallback, render_options
from app.geo.geocode import extract_place, geocode
from app.i18n.templates import t
from app.llm.gemini import GeminiError, generate_json
from app.models import CareLevel, GeoPoint, InboundMessage, Profile, RenderedOption, Verification
from app.triage.assess import LOCATION_QUESTION, bump_if_unsure, first_aid_reply, level_of_care

log = logging.getLogger(__name__)

VERIFY_BUDGET_S = 4.0
LEVEL_WAIT_S = 8.0
_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="pipeline")

# Injuries where an X-ray may be needed. Used only to prefer places with X-ray; places
# with unknown X-ray still show (find_care keeps them).
_XRAY_WORDS = re.compile(
    r"\b(sprain\w*|broke|broken|break|fractur\w*|twist\w*|rolled|fell|fall|fallen|ankle|wrist|knee|"
    r"elbow|shoulder|finger|toe|foot|hand|arm|leg|rib|torc\w*|esguince|fractura|roto|rota|quebr\w*|"
    r"tobillo|muneca|muñeca|rodilla|codo|hombro|dedo|pie|mano|brazo|pierna|costilla|ca[ií]\w*)\b",
    re.IGNORECASE,
)


def needs_xray(symptom_text: str) -> bool:
    return bool(_XRAY_WORDS.search(symptom_text or ""))


def _lang(profile: Profile) -> str:
    return "es" if (profile.language or "en").lower().startswith("es") else "en"


def _store():
    from app.conversation import store  # #9; imported late so this module loads without it
    return store


def _level(text: str, profile: Profile) -> tuple[str, float]:
    try:
        level, conf = level_of_care(text, profile)
        return level.value, conf
    except GeminiError:
        log.exception("level_of_care failed; using urgent care with zero confidence")
        return CareLevel.URGENT_CARE.value, 0.0


# --------------------------------------------------------------------------- Text 1

def first_reply(msg: InboundMessage, profile: Profile) -> list[str]:
    level_future = _pool.submit(_level, msg.text, profile)
    triage = first_aid_reply(msg.text, profile)
    store = _store()
    if triage.red_flag:
        level_future.cancel()
        from app.followup.scheduler import followup_context

        _, old_context = store.get_conversation(profile.user_id)
        store.set_conversation(profile.user_id, "idle", followup_context(old_context))
        return [triage.reply]

    context: dict = {"symptom_text": msg.text}
    try:
        level, conf = level_future.result(timeout=LEVEL_WAIT_S)
        context.update(level_of_care=level, level_confidence=conf)
    except FutureTimeout:
        log.warning("level_of_care still running after Text 1; Text 2 will compute it")
    store.set_conversation(profile.user_id, "awaiting_location", context)
    return [triage.reply]


# --------------------------------------------------------------------------- Text 2

def _verify(location) -> Verification | None:
    try:
        from app.care.verify import verify_live
    except ImportError:
        return None
    future = _pool.submit(verify_live, location)
    try:
        return future.result(timeout=VERIFY_BUDGET_S)
    except NotImplementedError:
        return None
    except FutureTimeout:
        log.warning("verify_live over %.1fs for %s; rendering without it", VERIFY_BUDGET_S, location.id)
        return None
    except Exception:
        log.exception("verify_live failed for %s", location.id)
        return None


def _finish(profile: Profile, symptom_text: str, level: CareLevel, location_ids: list[str],
            point: GeoPoint | None = None) -> None:
    """Keep only what the follow-up and after_answer need (no exact address: the place they
    named, as a map point), schedule the follow-up, go idle."""
    context = {
        "symptom_text": symptom_text,
        "last_level_of_care": level.value,
        "recommended_location_ids": location_ids,
        "answered_at": datetime.now(timezone.utc).isoformat(),
    }
    if point is not None:
        context["point"] = {"lat": point.lat, "lon": point.lon, "label": point.label}
    _store().set_conversation(profile.user_id, "idle", context)
    try:
        from app.followup.scheduler import schedule_followup
        schedule_followup(profile.user_id, symptom_text)
    except NotImplementedError:
        pass
    except Exception:
        log.exception("schedule_followup failed")


def options_reply(msg: InboundMessage, profile: Profile, symptom_text: str) -> list[str]:
    start = time.monotonic()
    lang = _lang(profile)
    store = _store()

    place = extract_place(msg.text)
    point = geocode(place) if place else None
    if point is None:  # stay in awaiting_location, context unchanged
        if place is None:  # they named no place at all ("I don't know how to get there")
            return [t("pipeline.ask_place_help", lang)]
        return [t("pipeline.ask_zip", lang)]

    _, context = store.get_conversation(profile.user_id)
    if context.get("level_of_care"):
        level_value, conf = context["level_of_care"], float(context.get("level_confidence", 0.0))
    else:
        level_value, conf = _level(symptom_text, profile)
    return _options_at(profile, symptom_text, level_value, conf, point, start)


def _options_at(profile: Profile, symptom_text: str, level_value: str, conf: float, point: GeoPoint,
                start: float) -> list[str]:
    lang = _lang(profile)
    level = bump_if_unsure(CareLevel(level_value), conf)
    if level is CareLevel.SELF_CARE:
        level = CareLevel.PRIMARY_CARE  # they asked where to go; show a clinic, not nothing

    candidates, radius = find_candidates_widening(profile, level, point, needs_xray=needs_xray(symptom_text))
    if not candidates:
        _finish(profile, symptom_text, level, [], point)
        log.info("options_reply: no candidates within %.0f mi of %s (%.1fs)", radius, point.label,
                 time.monotonic() - start)
        return [render_fallback(profile, lang)]

    ranked = rank(candidates, profile, symptom_text, level)
    by_id = {c.id: c for c in candidates}
    chosen = [by_id[r.location_id] for r in ranked]
    costs = [estimate_cost(loc, profile, level) for loc in chosen]
    verification = _verify(chosen[0])
    options = [
        RenderedOption(location=loc, reason=r.reason, cost=cost, verification=verification if i == 0 else None)
        for i, (loc, r, cost) in enumerate(zip(chosen, ranked, costs))
    ]
    text = render_options(options, lang)
    _finish(profile, symptom_text, level, [loc.id for loc in chosen], point)
    log.info("options_reply: %d options, level %s, %.0f mi, %.1fs", len(options), level.value, radius,
             time.monotonic() - start)
    return [text]


# --------------------------------------------------------------------------- after Text 2

RECENT = timedelta(hours=12)

AFTER_SYSTEM = """\
A person texted a health assistant about a problem and already got first-aid steps and a list
of nearby places to go. Now they sent another message. Sort it:
directions: they want to know how to get to a place we sent, its address, or a map.
update: more about the same problem (it hurts more, it might be broken, it is swelling, it is
  bleeding again, a new detail about it).
new: a different health problem or a new question unrelated to the first one.
other: thanks, ok, goodbye, or anything else that needs no new advice."""

AFTER_SCHEMA = {
    "type": "object",
    "properties": {"intent": {"type": "string", "enum": ["directions", "update", "new", "other"]}},
    "required": ["intent"],
}


def is_recent(context: dict, now: datetime | None = None) -> bool:
    """True when the last answer was sent within RECENT, so the next message is read in context."""
    if not context.get("recommended_location_ids") or not context.get("answered_at"):
        return False
    try:
        answered = datetime.fromisoformat(context["answered_at"])
    except (TypeError, ValueError):
        return False
    return (now or datetime.now(timezone.utc)) - answered <= RECENT


def _sort(prev: str, text: str) -> str:
    try:
        out = generate_json(prompt=f"First message: {prev}\nNew message: {text}", schema=AFTER_SCHEMA,
                            system=AFTER_SYSTEM)
        intent = out.get("intent")
    except GeminiError:
        log.exception("after_answer: Gemini failed; treating it as an update")
        intent = "update"  # the safe reading: re-answer with care options
    return intent if intent in ("directions", "update", "new", "other") else "update"


def _locations_by_id(ids: list[str]) -> list[dict]:
    from app.db import get_conn

    with get_conn() as conn:
        rows = conn.execute("SELECT id, name, address FROM locations WHERE id = ANY(%s)", (ids,)).fetchall()
    by_id = {r["id"]: r for r in rows}
    return [by_id[i] for i in ids if i in by_id]


def directions_text(rows: list[dict], lang: str) -> str:
    """Name, address, and an Apple Maps link per place, all from the database."""
    lines = [t("pipeline.directions_intro", lang)]
    for i, r in enumerate(rows, 1):
        url = "https://maps.apple.com/?daddr=" + quote_plus(r["address"])
        lines.append(f"{i}. {r['name']}\n{r['address']}\n{url}")
    return "\n\n".join(lines)


def after_answer(msg: InboundMessage, profile: Profile, context: dict) -> list[str] | None:
    """The next message after Text 2. None means: a new question, let the router start over."""
    lang = _lang(profile)
    prev = context.get("symptom_text") or ""
    intent = _sort(prev, msg.text)
    log.info("after_answer: %s", intent)

    if intent == "new":
        return None
    if intent == "other":
        return [t("pipeline.after_other", lang)]
    if intent == "directions":
        try:
            rows = _locations_by_id(context["recommended_location_ids"])
        except Exception:
            log.exception("after_answer: could not load locations")
            rows = []
        return [directions_text(rows, lang)] if rows else [t("pipeline.after_other", lang)]

    # update: same problem, more detail. Fresh first aid, then options for the saved place.
    start = time.monotonic()
    symptom = f"{prev}. Then: {msg.text}" if prev else msg.text
    triage = first_aid_reply(symptom, profile)
    store = _store()
    if triage.red_flag:
        from app.followup.scheduler import followup_context

        store.set_conversation(profile.user_id, "idle", followup_context(context))
        return [triage.reply]
    body = triage.reply
    for q in LOCATION_QUESTION.values():
        if body.endswith(q):
            body = body[: -len(q)].rstrip()
    saved = context.get("point")
    if not saved:  # no saved place: ask, like a new question
        store.set_conversation(profile.user_id, "awaiting_location", {"symptom_text": symptom})
        return [triage.reply]
    point = GeoPoint(lat=saved["lat"], lon=saved["lon"], label=saved.get("label") or "")
    level_value, conf = _level(symptom, profile)
    # Never step down from the level we already sent them to.
    last = context.get("last_level_of_care")
    if last in {lv.value for lv in CareLevel}:
        from app.models import LEVEL_ORDER

        if LEVEL_ORDER.index(CareLevel(last)) > LEVEL_ORDER.index(CareLevel(level_value)):
            level_value, conf = last, 1.0
    return [body] + _options_at(profile, symptom, level_value, conf, point, start)
