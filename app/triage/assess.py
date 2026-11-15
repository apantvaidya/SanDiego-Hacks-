"""First-aid reply and level of care. Never diagnose."""
from app.models import LEVEL_ORDER, CareLevel, Profile, TriageResult


def first_aid_reply(text: str, profile: Profile) -> TriageResult:
    """Gemini: second-net red-flag check + general first-aid steps + ER warning signs,
    ending with 'Where are you right now?'. Reply in profile.language. Keep it short."""
    raise NotImplementedError


def level_of_care(text: str, profile: Profile) -> tuple[CareLevel, float]:
    raise NotImplementedError


def bump_if_unsure(level: CareLevel, confidence: float, threshold: float = 0.7) -> CareLevel:
    """Pure. If confidence < threshold, move one step up LEVEL_ORDER (ER stays ER)."""
    raise NotImplementedError
