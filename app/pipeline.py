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

Latency: Text 2 must arrive in about 10 s. Measured parts: sd_places hit about 0 s
(Gemini place extraction about 1 s otherwise), candidate query under 0.5 s, rank about 1 s.
verify_live gets at most VERIFY_BUDGET_S; when it is slower the card says "checked <crawl
time>" instead of "verified", which is the honest label.
"""
from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from app.care.cost import estimate_cost
from app.care.find_care import find_candidates_widening
from app.care.rank import rank
from app.care.render import render_fallback, render_options
from app.geo.geocode import extract_place, geocode
from app.i18n.templates import t
from app.llm.gemini import GeminiError
from app.models import CareLevel, InboundMessage, Profile, RenderedOption, Verification
from app.triage.assess import bump_if_unsure, first_aid_reply, level_of_care

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


def _finish(profile: Profile, symptom_text: str, level: CareLevel, location_ids: list[str]) -> None:
    """Keep only what the follow-up needs, schedule it, go idle."""
    _store().set_conversation(profile.user_id, "idle", {
        "symptom_text": symptom_text,
        "last_level_of_care": level.value,
        "recommended_location_ids": location_ids,
    })
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
    if point is None:
        return [t("pipeline.ask_zip", lang)]  # stay in awaiting_location, context unchanged

    _, context = store.get_conversation(profile.user_id)
    if context.get("level_of_care"):
        level_value, conf = context["level_of_care"], float(context.get("level_confidence", 0.0))
    else:
        level_value, conf = _level(symptom_text, profile)
    level = bump_if_unsure(CareLevel(level_value), conf)
    if level is CareLevel.SELF_CARE:
        level = CareLevel.PRIMARY_CARE  # they asked where to go; show a clinic, not nothing

    candidates, radius = find_candidates_widening(profile, level, point, needs_xray=needs_xray(symptom_text))
    if not candidates:
        _finish(profile, symptom_text, level, [])
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
    _finish(profile, symptom_text, level, [loc.id for loc in chosen])
    log.info("options_reply: %d options, level %s, %.0f mi, %.1fs", len(options), level.value, radius,
             time.monotonic() - start)
    return [text]
