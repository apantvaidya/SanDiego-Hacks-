"""Candidate query: one PostGIS query in Neon."""
from app.models import CareLevel, GeoPoint, Location, Profile


def find_candidates(profile: Profile, level: CareLevel, point: GeoPoint,
                    radius_miles: float = 5, limit: int = 10) -> list[Location]:
    raise NotImplementedError


def is_open_now(hours: dict) -> bool:
    """Pure helper, America/Los_Angeles time."""
    raise NotImplementedError
