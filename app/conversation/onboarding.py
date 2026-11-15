"""Text onboarding: card photo (or NONE / MEDI-CAL / OTHER) -> confirm -> household ->
income bracket -> language. Never store member ID or the image."""
from app.models import CardExtraction, InboundMessage, User


def handle_onboarding(user: User, msg: InboundMessage) -> list[str]:
    raise NotImplementedError


def extract_card(image: bytes) -> CardExtraction:
    raise NotImplementedError
