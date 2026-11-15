"""Sendblue iMessage client. Docs: https://docs.sendblue.com

Free shared-line plan notes (verified Nov 14, 2026):
- `from_number` (our Sendblue line) is required on every send.
- A recipient must be added as a contact and text our line once before messages route.
- Inbound media arrives as a CDN link in `media_url` that expires after 30 days.
- Sendblue retries the webhook if we don't respond, so the webhook must return fast.
"""
from __future__ import annotations

import logging
from datetime import datetime

import httpx

from app.config import settings
from app.models import InboundMessage

log = logging.getLogger(__name__)

BASE_URL = "https://api.sendblue.com"
# Sendblue's edge rejects some default client user agents with 403.
USER_AGENT = "care-navigator/0.1"


def _headers() -> dict[str, str]:
    return {
        "sb-api-key-id": settings.sendblue_api_key,
        "sb-api-secret-key": settings.sendblue_api_secret,
        "User-Agent": USER_AGENT,
    }


def _client() -> httpx.Client:
    return httpx.Client(base_url=BASE_URL, headers=_headers(), timeout=15.0)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_inbound(payload: dict) -> InboundMessage | None:
    """Turn a Sendblue receive webhook (or a v2 messages list item) into an InboundMessage.

    Returns None for anything that is not a new message from a user: outbound echoes,
    status callbacks, typing indicators, and group messages.
    """
    if payload.get("is_outbound"):
        return None
    if payload.get("message_type") == "group" or payload.get("group_id"):
        return None
    phone = payload.get("number") or payload.get("from_number")
    if not phone:
        return None
    media_url = payload.get("media_url") or ""
    return InboundMessage(
        phone=phone,
        text=(payload.get("content") or "").strip(),
        media_urls=[media_url] if media_url else [],
        received_at=_parse_dt(payload.get("date_sent")),
        message_id=payload.get("message_handle"),
    )


def send_text(phone: str, text: str) -> None:
    """Send an iMessage (falls back to SMS on Sendblue's side if iMessage is unavailable)."""
    body = {"number": phone, "content": text}
    if settings.sendblue_from_number:
        body["from_number"] = settings.sendblue_from_number
    with _client() as client:
        resp = client.post("/api/send-message", json=body)
    if resp.status_code >= 400:
        log.error("sendblue send failed %s: %s", resp.status_code, resp.text[:300])
        resp.raise_for_status()
    data = resp.json()
    if data.get("error_code") or str(data.get("status", "")).upper() == "ERROR":
        log.error("sendblue send error: %s", data)
        raise RuntimeError(f"Sendblue send error: {data.get('error_message') or data}")


def download_media(url: str) -> bytes:
    """Inbound media CDN links expire. Download as soon as the message arrives."""
    with httpx.Client(timeout=30.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        resp = client.get(url)
    resp.raise_for_status()
    return resp.content


def list_inbound(created_after: datetime | None = None, limit: int = 50) -> list[dict]:
    """Recent inbound messages, oldest first. Used by the poller when webhooks are unavailable."""
    params: dict[str, str | int] = {
        "is_outbound": "false",
        "limit": limit,
        "order_by": "createdAt",
        "order_direction": "asc",
    }
    if settings.sendblue_from_number:
        params["sendblue_number"] = settings.sendblue_from_number
    if created_after:
        params["created_at_gte"] = created_after.isoformat()
    with _client() as client:
        resp = client.get("/api/v2/messages", params=params)
    resp.raise_for_status()
    return resp.json().get("data", [])


def get_webhooks() -> dict:
    with _client() as client:
        resp = client.get("/api/account/webhooks")
    resp.raise_for_status()
    return resp.json()


def set_receive_webhook(url: str, secret: str | None = None) -> dict:
    """Replace the receive webhook list with this one URL (account-level setting)."""
    hook: dict | str = {"url": url, "secret": secret} if secret else url
    with _client() as client:
        resp = client.put("/api/account/webhooks", json={"webhooks": {"receive": [hook]}})
    resp.raise_for_status()
    return resp.json()
