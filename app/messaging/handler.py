"""Shared inbound handler used by both the webhook and the poller.

Dedupes on message_id, downloads media right away, calls the conversation router,
and sends each reply. Until the router (#9) is built, it echoes the message back.
"""
from __future__ import annotations

import logging
import threading
from collections import OrderedDict

from app.messaging import download_media, send_text
from app.models import InboundMessage

log = logging.getLogger(__name__)

_seen: OrderedDict[str, None] = OrderedDict()
_seen_lock = threading.Lock()
_SEEN_MAX = 2000


def already_seen(message_id: str | None) -> bool:
    """Record message_id; return True if it was handled before (Sendblue retries webhooks)."""
    if not message_id:
        return False
    with _seen_lock:
        if message_id in _seen:
            return True
        _seen[message_id] = None
        if len(_seen) > _SEEN_MAX:
            _seen.popitem(last=False)
        return False


def is_seen(message_id: str | None) -> bool:
    """Peek without recording."""
    with _seen_lock:
        return bool(message_id) and message_id in _seen


def _echo(msg: InboundMessage, media: list[bytes]) -> list[str]:
    parts = []
    if msg.text:
        parts.append(f'Got it: "{msg.text}"')
    for blob in media:
        parts.append(f"Got your photo ({len(blob) // 1024} KB).")
    return [" ".join(parts) or "Got your message."]


def route(msg: InboundMessage, media: list[bytes]) -> list[str]:
    try:
        from app.conversation.router import handle

        return handle(msg)
    except NotImplementedError:
        return _echo(msg, media)


def handle_inbound(msg: InboundMessage) -> None:
    if already_seen(msg.message_id):
        log.info("duplicate message %s ignored", msg.message_id)
        return
    media: list[bytes] = []
    for url in msg.media_urls:
        try:
            media.append(download_media(url))
        except Exception:
            log.exception("media download failed for %s", msg.message_id)
    try:
        replies = route(msg, media)
    except Exception:
        log.exception("router failed for %s", msg.message_id)
        replies = ["Sorry, something went wrong on our side. If this is an emergency, call 911."]
    for reply in replies:
        try:
            send_text(msg.phone, reply)
        except Exception:
            log.exception("send failed for %s", msg.message_id)
