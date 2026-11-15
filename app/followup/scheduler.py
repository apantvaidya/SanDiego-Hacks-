"""Next-day follow-up. 'Reply 1 better, 2 same, 3 worse.' Worse re-runs triage one level up."""
from uuid import UUID

from app.models import Profile, User


def schedule_followup(user_id: UUID, topic: str, hours: int = 20) -> None:
    raise NotImplementedError


def run_due() -> int:
    raise NotImplementedError


def followup_now(user_id: UUID) -> list[str]:
    raise NotImplementedError


def handle_followup_reply(user: User, profile: Profile, text: str) -> list[str]:
    """Called by the router in state awaiting_followup_reply. 1 better, 2 same, 3 worse.
    Worse re-runs triage one level above context.last_level_of_care. Set the next state via
    app.conversation.store.set_conversation."""
    raise NotImplementedError
