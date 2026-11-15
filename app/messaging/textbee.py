"""Fallback provider: textbee (Android phone as SMS gateway, webhooks, 300 free msgs/month).
Same interface as sendblue.py. Only build if Sendblue fails build step 1."""
from app.models import InboundMessage


def parse_inbound(payload: dict) -> InboundMessage:
    raise NotImplementedError


def send_text(phone: str, text: str) -> None:
    raise NotImplementedError


def download_media(url: str) -> bytes:
    raise NotImplementedError
