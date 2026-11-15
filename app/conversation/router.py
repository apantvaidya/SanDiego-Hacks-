"""Conversation state machine. Order: command -> onboarding -> safety.screen -> pipeline."""
from app.models import InboundMessage


def handle(msg: InboundMessage) -> list[str]:
    raise NotImplementedError
