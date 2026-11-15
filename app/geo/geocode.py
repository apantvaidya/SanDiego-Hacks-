"""Geocode free-text locations. sd_places lookup first, Nominatim second (1 req/s, real User-Agent).
If both fail, the caller asks for a ZIP code."""
from app.models import GeoPoint


def extract_place(text: str) -> str | None:
    """Gemini pulls the place out of the reply: "I'm in La Jolla" -> "La Jolla"."""
    raise NotImplementedError


def geocode(text: str) -> GeoPoint | None:
    raise NotImplementedError
