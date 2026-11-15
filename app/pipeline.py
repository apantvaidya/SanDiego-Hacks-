"""End-to-end question flow. Owned by the integration issue.

first_reply: safety already passed. Calls triage.first_aid_reply, stores symptom in
conversation context, sets state awaiting_location.

options_reply: geocode -> level_of_care + bump_if_unsure -> find_candidates (widen once)
-> rank -> estimate_cost for each -> verify_live on #1 -> render_options, or render_fallback.
"""
from app.models import InboundMessage, Profile


def first_reply(msg: InboundMessage, profile: Profile) -> list[str]:
    raise NotImplementedError


def options_reply(msg: InboundMessage, profile: Profile, symptom_text: str) -> list[str]:
    raise NotImplementedError
