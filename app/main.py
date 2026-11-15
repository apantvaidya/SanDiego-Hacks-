"""FastAPI app and Sendblue webhook."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from app.config import settings
from app.messaging import parse_inbound
from app.messaging.handler import handle_inbound

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop = None
    if settings.messaging_inbound == "poll":
        from app.messaging.poller import start_background

        stop = start_background()
    yield
    if stop:
        stop.set()


app = FastAPI(title="Care Navigator", lifespan=lifespan)

from app.signup import router as signup_router  # noqa: E402  GET/POST /start

app.include_router(signup_router)


@app.get("/health")
def health() -> dict:
    return {"ok": True, "inbound": settings.messaging_inbound, "provider": settings.messaging_provider}


def _secret_ok(request: Request) -> bool:
    """Sendblue sends the configured webhook secret in a request header. The docs don't
    name the header, so accept it in any header value."""
    secret = settings.sendblue_webhook_secret
    if not secret:
        return True
    return any(value == secret for value in request.headers.values())


@app.post("/webhooks/sendblue")
async def sendblue_webhook(request: Request, background: BackgroundTasks) -> dict:
    """Return fast (Sendblue retries if we don't respond) and do the work in the background."""
    if not _secret_ok(request):
        log.warning("webhook rejected: bad secret; header names=%s", list(request.headers.keys()))
        raise HTTPException(status_code=401, detail="bad secret")
    payload = await request.json()
    msg = parse_inbound(payload)
    if msg is None:
        return {"ok": True, "ignored": True}
    background.add_task(handle_inbound, msg)
    return {"ok": True}
