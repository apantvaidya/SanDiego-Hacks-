"""Conversation state machine.

Order for every inbound message:
  1. Load or create the user.
  2. Opted out: only START is answered. Everything else gets no reply.
  3. STOP / START / HELP and #commands.
  4. Safety screen (always before any LLM call, in any state, including onboarding).
  5. No active profile, or an onboarding state: onboarding (#10).
  6. awaiting_followup_reply: follow-up handler (#16).
  7. awaiting_location: pipeline.options_reply (#17), using the saved symptom.
  8. Otherwise: pipeline.first_reply (#17).

State ownership: the pipeline and onboarding set their own next state through
app.conversation.store (see docs/interfaces.md). The router only sets state for safety
matches and for the placeholder replies used while those modules are still stubs.
"""
from __future__ import annotations

import logging

from app.conversation import commands, store
from app.models import InboundMessage
from app.safety.keywords import screen

log = logging.getLogger(__name__)

ONBOARDING_STATES = {
    "new",
    "onboarding_card",
    "onboarding_confirm",
    "onboarding_household",
    "onboarding_income",
    "onboarding_language",
}

PLACEHOLDER = {
    "no_profile": {
        "en": "Hi! I'm Care Navigator. To try the demo, text #profile kaiser, #profile medi_cal, or #profile uninsured.",
        "es": "¡Hola! Soy Care Navigator. Para probar la demo, escribe #profile kaiser, #profile medi_cal o #profile uninsured.",
    },
    "first_reply": {
        "en": "Got it. Where are you right now? (Full answers are still being built.)",
        "es": "Entendido. ¿Dónde estás ahora? (Las respuestas completas aún se están construyendo.)",
    },
    "options_reply": {
        "en": "Thanks. Nearby options with costs are still being built.",
        "es": "Gracias. Las opciones cercanas con costos aún se están construyendo.",
    },
}


def _ph(key: str, language: str) -> str:
    return PLACEHOLDER[key]["es" if language == "es" else "en"]


def handle(msg: InboundMessage) -> list[str]:
    user = store.get_or_create_user(msg.phone)
    text = msg.text or ""

    # 2. Opted out: stay silent unless they text START.
    if user.opted_out and not commands.is_start(text):
        return []

    # 3. STOP / START / HELP / #commands.
    if text:
        replies = commands.handle_command(user, text)
        if replies is not None:
            return replies

    state, context = store.get_conversation(user.id)
    profile = store.get_active_profile(user)
    language = profile.language if profile else user.language

    # 4. Safety first, in every state.
    if text:
        safety = screen(text, language)
        if safety.matched:
            log.warning("safety match: %s", safety.category)
            if state not in ONBOARDING_STATES:
                store.set_conversation(user.id, "idle", {})
            return [safety.reply]

    # 5. Onboarding.
    if profile is None or state in ONBOARDING_STATES:
        try:
            from app.conversation.onboarding import handle_onboarding

            return handle_onboarding(user, msg)
        except NotImplementedError:
            return [_ph("no_profile", language)]

    # 6. Follow-up reply.
    if state == "awaiting_followup_reply":
        try:
            from app.followup.scheduler import handle_followup_reply

            return handle_followup_reply(user, profile, text)
        except NotImplementedError:
            store.set_conversation(user.id, "idle", {})
            # fall through and treat it as a new question

    from app import pipeline

    # 7. Waiting for a location.
    if state == "awaiting_location" and context.get("symptom_text"):
        try:
            return pipeline.options_reply(msg, profile, context["symptom_text"])
        except NotImplementedError:
            store.set_conversation(user.id, "idle", {})
            return [_ph("options_reply", language)]

    # 8. New question.
    try:
        return pipeline.first_reply(msg, profile)
    except NotImplementedError:
        store.set_conversation(user.id, "awaiting_location", {"symptom_text": text})
        return [_ph("first_reply", language)]
