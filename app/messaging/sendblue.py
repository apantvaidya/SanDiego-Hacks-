"""Sendblue iMessage client. See https://docs.sendblue.com"""
from app.models import InboundMessage


def parse_inbound(payload: dict) -> InboundMessage:
    raise NotImplementedError


def send_text(phone: str, text: str) -> None:
    raise NotImplementedError


def download_media(url: str) -> bytes:
    """Inbound media CDN links expire. Download as soon as the webhook arrives."""
    raise NotImplementedError
