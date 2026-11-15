"""Messaging facade. Import from here, not from a provider module:

    from app.messaging import parse_inbound, send_text, download_media

The provider is chosen by MESSAGING_PROVIDER (sendblue default, textbee fallback).
"""
from app.config import settings

if settings.messaging_provider == "textbee":
    from app.messaging.textbee import download_media, parse_inbound, send_text
else:
    from app.messaging.sendblue import download_media, parse_inbound, send_text

__all__ = ["parse_inbound", "send_text", "download_media"]
