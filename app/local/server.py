"""Local chat page: the whole assistant in a browser, no phone, Sendblue or tunnel. Owned by #43.

    uv run python -m app.local            # http://127.0.0.1:8001
    uv run python -m app.local --port 9000

Every message goes through the same router as iMessage (app.conversation.router.handle),
against the real database and Gemini, so answers match the phone. Nothing is sent by text:
the replies come back in the HTTP response. Each browser gets its own fake 555 number
(kept in localStorage), so it is a separate user; "New user" starts over.

Card photos: the page uploads the image, the server holds the bytes in memory under a
local:// URL, and onboarding's download_media call takes them out (and drops them) once.
Nothing is written to disk.
"""
from __future__ import annotations

import base64
import logging
import re
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.models import InboundMessage

log = logging.getLogger(__name__)

PAGE = Path(__file__).with_name("page.html")
PHONE_RE = re.compile(r"^\+1555\d{7}$")  # fake numbers only: never a real person's phone
MAX_IMAGE_BYTES = 8 * 1024 * 1024
LOCAL_PREFIX = "local://"

_media: dict[str, bytes] = {}
_media_lock = threading.Lock()


def put_media(data: bytes) -> str:
    url = f"{LOCAL_PREFIX}{uuid.uuid4()}"
    with _media_lock:
        _media[url] = data
    return url


def take_media(url: str) -> bytes:
    with _media_lock:
        data = _media.pop(url, None)
    if data is None:
        raise KeyError(url)
    return data


def install_media_hook() -> None:
    """Make app.messaging.download_media serve local:// uploads; other URLs go to the provider."""
    import app.messaging as messaging

    real = messaging.download_media
    if getattr(real, "_local_hook", False):
        return

    def download_media(url: str) -> bytes:
        return take_media(url) if url.startswith(LOCAL_PREFIX) else real(url)

    download_media._local_hook = True  # type: ignore[attr-defined]
    messaging.download_media = download_media


class SendIn(BaseModel):
    phone: str
    text: str = ""
    image_b64: str | None = None  # data URL or bare base64


class SendOut(BaseModel):
    replies: list[str]


def _decode_image(value: str) -> bytes:
    if value.startswith("data:"):
        value = value.split(",", 1)[-1]
    try:
        data = base64.b64decode(value, validate=True)
    except ValueError as e:
        raise HTTPException(400, "image is not base64") from e
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "image over 8 MB")
    return data


def handle(phone: str, text: str, image: bytes | None = None) -> list[str]:
    from app.conversation.router import handle as route

    media = [put_media(image)] if image else []
    try:
        return route(InboundMessage(phone=phone, text=text.strip(), media_urls=media,
                                    message_id=f"local-{uuid.uuid4()}"))
    except Exception:
        log.exception("router failed")
        return ["Sorry, something went wrong on our side. If this is an emergency, call 911."]
    finally:
        for url in media:  # drop the photo even if onboarding never read it
            with _media_lock:
                _media.pop(url, None)


def create_app() -> FastAPI:
    install_media_hook()
    app = FastAPI(title="Care Navigator (local)")
    from app.signup import router as signup_router

    app.include_router(signup_router)  # /start signup page

    @app.get("/", response_class=HTMLResponse)
    def page() -> str:
        return PAGE.read_text()

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "mode": "local"}

    @app.post("/send", response_model=SendOut)
    def send(body: SendIn) -> SendOut:
        if not PHONE_RE.match(body.phone):
            raise HTTPException(400, "phone must be a fake +1555 number")
        image = _decode_image(body.image_b64) if body.image_b64 else None
        if not body.text.strip() and image is None:
            raise HTTPException(400, "empty message")
        return SendOut(replies=handle(body.phone, body.text, image))

    return app
