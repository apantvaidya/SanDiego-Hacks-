"""Sendblue helper commands.

    uv run python -m scripts.sendblue_setup show                 # current webhooks
    uv run python -m scripts.sendblue_setup webhook <public-url> # point receive webhook at <url>/webhooks/sendblue
    uv run python -m scripts.sendblue_setup send "<text>"        # text DEMO_PHONE
    uv run python -m scripts.sendblue_setup inbox                # last inbound messages
"""
import json
import sys

from app.config import settings
from app.messaging import sendblue


def main(argv: list[str]) -> None:
    if not argv or argv[0] == "show":
        print(json.dumps(sendblue.get_webhooks(), indent=2))
    elif argv[0] == "webhook":
        url = argv[1].rstrip("/") + "/webhooks/sendblue"
        print(json.dumps(sendblue.set_receive_webhook(url, settings.sendblue_webhook_secret or None), indent=2))
    elif argv[0] == "send":
        sendblue.send_text(settings.demo_phone, argv[1])
        print("sent")
    elif argv[0] == "inbox":
        for m in sendblue.list_inbound(limit=10):
            print(m.get("date_sent"), m.get("service"), repr((m.get("content") or "")[:60]), m.get("media_url") or "")
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
