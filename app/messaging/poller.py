"""Poll Sendblue for new inbound messages. Fallback when the webhook can't reach us
(no public URL). Enable with MESSAGING_INBOUND=poll. Run standalone with:

    uv run python -m app.messaging.poller
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.messaging.handler import already_seen, handle_inbound, is_seen
from app.messaging.sendblue import list_inbound, parse_inbound

log = logging.getLogger(__name__)


def poll_once(since: datetime) -> datetime:
    """Handle messages created at or after `since`. Returns the new cursor."""
    newest = since
    for item in list_inbound(created_after=since):
        msg = parse_inbound(item)
        if msg is None or is_seen(msg.message_id):
            continue
        if msg.received_at and msg.received_at > newest:
            newest = msg.received_at
        handle_inbound(msg)  # created_at_gte overlaps the last message; is_seen skips it
    return newest


def run_forever(stop: threading.Event | None = None) -> None:
    # Only answer messages that arrive after startup, plus a small overlap.
    since = datetime.now(timezone.utc) - timedelta(seconds=5)
    # Mark anything already in that window as seen so a restart doesn't re-reply.
    try:
        for item in list_inbound(created_after=since):
            already_seen(item.get("message_handle"))
    except Exception:
        log.exception("initial poll failed")
    log.info("polling Sendblue every %ss", settings.poll_interval_seconds)
    while not (stop and stop.is_set()):
        try:
            since = poll_once(since)
        except Exception:
            log.exception("poll failed")
        time.sleep(settings.poll_interval_seconds)


def start_background() -> threading.Event:
    stop = threading.Event()
    threading.Thread(target=run_forever, args=(stop,), daemon=True, name="sendblue-poller").start()
    return stop


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_forever()
