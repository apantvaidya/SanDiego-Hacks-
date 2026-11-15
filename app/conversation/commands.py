"""STOP, HELP, and demo commands: #profile <label>, #followup, #reset, #whoami.
STOP sets users.opted_out and cancels queued follow-ups. HELP explains the service plus the 911 / 988 notice."""
from app.models import User


def handle_command(user: User, text: str) -> list[str] | None:
    raise NotImplementedError
