"""Next-day follow-up. 'Reply 1 better, 2 same, 3 worse.' Worse re-runs triage one level up."""
from uuid import UUID


def schedule_followup(user_id: UUID, topic: str, hours: int = 20) -> None:
    raise NotImplementedError


def run_due() -> int:
    raise NotImplementedError


def followup_now(user_id: UUID) -> list[str]:
    raise NotImplementedError
