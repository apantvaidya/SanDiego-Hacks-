"""Geocode free-text locations. sd_places lookup first, Nominatim second (1 req/s, real User-Agent).
If both fail, the caller asks for a ZIP code. Owned by #5.

extract_place(text): "I'm near UCSD right now" -> "UCSD".
    A ZIP code or a known sd_places name in the text is returned without calling Gemini
    (saves about a second); otherwise Gemini pulls the place out.
geocode(text): GeoPoint or None.
    1. 5-digit ZIP -> the sd_places ZIP row, else Nominatim postal code search
    2. sd_places name or alias, exact, then as a whole phrase inside the text (longest first)
    3. Nominatim, biased (not bounded) to San Diego County; anything outside the county is
       rejected. Not bounded on purpose: bounded, "Los Angeles" matches a street called
       Los Angeles Place in San Diego; unbounded it resolves to LA and is rejected.
"""
from __future__ import annotations

import logging
import re
import threading
import time
import unicodedata

import httpx

from app.config import settings
from app.llm.gemini import GeminiError, generate_json
from app.models import GeoPoint

log = logging.getLogger(__name__)

# San Diego County, generous box. (lon_min, lat_min, lon_max, lat_max)
SD_BBOX = (-117.62, 32.52, -116.08, 33.52)
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_TIMEOUT_S = 6
_ZIP = re.compile(r"\b(9[12]\d{3})\b")

_nominatim_lock = threading.Lock()
_last_nominatim_call = 0.0
_places_cache: list[dict] | None = None


def normalize(text: str) -> str:
    """Lowercase, strip accents and punctuation, collapse spaces."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"[^a-z0-9 ]+", " ", text.replace("'", "").replace("’", ""))
    return " ".join(text.split())


def in_san_diego_county(lat: float, lon: float) -> bool:
    lon_min, lat_min, lon_max, lat_max = SD_BBOX
    return lat_min <= lat <= lat_max and lon_min <= lon <= lon_max


# --------------------------------------------------------------------------- sd_places

def _load_places() -> list[dict]:
    """All sd_places rows (about 75), cached for the process. Each row gets a list of
    normalized keys: the name plus every alias."""
    global _places_cache
    if _places_cache is None:
        from app.db import get_conn
        with get_conn() as conn:
            rows = conn.execute("SELECT name, aliases, lat, lon FROM sd_places").fetchall()
        _places_cache = [
            {**r, "keys": sorted({normalize(r["name"]), *(normalize(a) for a in r["aliases"] or [])}, key=len, reverse=True)}
            for r in rows
        ]
    return _places_cache


def _point(row: dict) -> GeoPoint:
    return GeoPoint(lat=float(row["lat"]), lon=float(row["lon"]), label=row["name"])


def lookup_place(text: str) -> GeoPoint | None:
    """sd_places only: exact name or alias first, then the longest name or alias that
    appears as a whole phrase inside the text."""
    norm = normalize(text)
    if not norm:
        return None
    places = _load_places()
    for row in places:
        if norm in row["keys"]:
            return _point(row)
    best: tuple[int, dict] | None = None
    padded = f" {norm} "
    for row in places:
        for key in row["keys"]:
            if len(key) >= 3 and f" {key} " in padded and (best is None or len(key) > best[0]):
                best = (len(key), row)
    return _point(best[1]) if best else None


# --------------------------------------------------------------------------- Nominatim

def _nominatim(params: dict) -> GeoPoint | None:
    """One Nominatim search, at most 1 request per second across threads."""
    global _last_nominatim_call
    lon_min, lat_min, lon_max, lat_max = SD_BBOX
    params = {**params, "format": "jsonv2", "limit": 1, "countrycodes": "us",
              "viewbox": f"{lon_min},{lat_max},{lon_max},{lat_min}", "bounded": 0}
    with _nominatim_lock:
        wait = 1.0 - (time.monotonic() - _last_nominatim_call)
        if wait > 0:
            time.sleep(wait)
        try:
            resp = httpx.get(NOMINATIM_URL, params=params, timeout=NOMINATIM_TIMEOUT_S,
                             headers={"User-Agent": settings.nominatim_user_agent})
            resp.raise_for_status()
            results = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            log.warning("nominatim failed: %s", e)
            return None
        finally:
            _last_nominatim_call = time.monotonic()
    if not results:
        return None
    lat, lon = float(results[0]["lat"]), float(results[0]["lon"])
    if not in_san_diego_county(lat, lon):
        return None
    label = results[0].get("name") or results[0].get("display_name", "").split(",")[0] or params.get("q", "")
    return GeoPoint(lat=lat, lon=lon, label=label)


# --------------------------------------------------------------------------- public

EXTRACT_SYSTEM = """\
A person in San Diego County was asked "Where are you right now?" and replied by text.
place: the place they named, as short as possible but specific enough to find on a map
(a neighborhood, city, landmark, cross streets, street address, business, or ZIP code).
Keep their spelling. null if they named no place ("at home", "at work").
neighborhood: the San Diego County neighborhood or city that place is in, only when you
are confident (for example "30th and University" is in North Park). null otherwise, and
null when place is already a neighborhood, city or ZIP."""

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "place": {"type": ["string", "null"]},
        "neighborhood": {"type": ["string", "null"]},
    },
    "required": ["place", "neighborhood"],
}


def extract_place(text: str) -> str | None:
    """Gemini pulls the place out of the reply: "I'm in La Jolla" -> "La Jolla"."""
    if not text or not text.strip():
        return None
    zip_match = _ZIP.search(text)
    if zip_match:
        return zip_match.group(1)
    try:
        known = lookup_place(text)
    except Exception:  # DB down: let Gemini and Nominatim try
        log.exception("extract_place: sd_places lookup failed")
        known = None
    if known:
        return known.label
    try:
        out = generate_json(prompt=f"Reply:\n{text}", schema=EXTRACT_SCHEMA, system=EXTRACT_SYSTEM)
    except GeminiError:
        log.exception("extract_place: Gemini failed, using the raw reply")
        return text.strip()
    place = out.get("place")
    if not isinstance(place, str) or not place.strip():
        return None
    place = place.strip()
    hood = out.get("neighborhood")
    # "30th and university, North Park": geocode() finds North Park in sd_places when
    # Nominatim cannot place an intersection.
    if isinstance(hood, str) and hood.strip() and normalize(hood) not in normalize(place):
        place = f"{place}, {hood.strip()}"
    return place


def geocode(text: str) -> GeoPoint | None:
    if not text or not text.strip():
        return None
    zip_match = _ZIP.search(text)
    if zip_match:
        z = zip_match.group(1)
        try:
            hit = lookup_place(z)
        except Exception:
            log.exception("geocode: sd_places lookup failed")
            hit = None
        return hit or _nominatim({"postalcode": z})
    try:
        hit = lookup_place(text)
    except Exception:
        log.exception("geocode: sd_places lookup failed")
        hit = None
    if hit:
        return hit
    return _nominatim({"q": text.strip()})


def reset_cache() -> None:
    """For tests: forget the cached sd_places rows."""
    global _places_cache
    _places_cache = None
