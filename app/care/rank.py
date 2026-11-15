"""Gemini ranks candidates and returns IDs + short reasons only. Code drops unknown IDs."""
from app.models import CareLevel, Location, Profile, RankedOption


def rank(candidates: list[Location], profile: Profile, symptom_text: str,
         level: CareLevel) -> list[RankedOption]:
    raise NotImplementedError
