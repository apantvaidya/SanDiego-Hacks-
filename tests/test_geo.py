"""Tests for #5 geocoding. sd_places and Nominatim are faked by default.
Live tests (Neon sd_places + Nominatim + Gemini) run with RUN_LIVE_GEO=1."""
import os

import pytest

from app.geo import geocode as geo
from app.geo.geocode import extract_place, geocode, in_san_diego_county, lookup_place, normalize
from app.llm.gemini import GeminiError
from app.models import GeoPoint

PLACES = [
    {"name": "La Jolla", "aliases": ["lajolla"], "lat": 32.8459, "lon": -117.2576},
    {"name": "UCSD", "aliases": ["uc san diego", "ucsd campus"], "lat": 32.8792, "lon": -117.2311},
    {"name": "North Park", "aliases": [], "lat": 32.7484, "lon": -117.1302},
    {"name": "Pacific Beach", "aliases": ["pb"], "lat": 32.7978, "lon": -117.2403},
    {"name": "Rancho Peñasquitos", "aliases": ["rancho penasquitos"], "lat": 32.9577, "lon": -117.1285},
    {"name": "Midway District", "aliases": ["midway", "sports arena"], "lat": 32.7499, "lon": -117.2062},
    {"name": "92103", "aliases": [], "lat": 32.7469, "lon": -117.1663},
]


@pytest.fixture(autouse=True)
def fake_places(monkeypatch):
    if os.getenv("RUN_LIVE_GEO") == "1":
        geo.reset_cache()
        yield
        return
    rows = [{**r, "keys": sorted({normalize(r["name"]), *map(normalize, r["aliases"])}, key=len, reverse=True)}
            for r in PLACES]
    monkeypatch.setattr(geo, "_places_cache", rows)
    yield


@pytest.fixture
def nominatim(monkeypatch):
    calls = []

    def fake(params):
        calls.append(params)
        q = params.get("q", "") + params.get("postalcode", "")
        if "petco" in q.lower():
            return GeoPoint(32.7073, -117.1566, "Petco Park")
        return None

    monkeypatch.setattr(geo, "_nominatim", fake)
    return calls


@pytest.fixture
def gemini(monkeypatch):
    replies = {}

    def fake(prompt, schema, image=None, system=None):
        for k, v in replies.items():
            if k in prompt:
                if isinstance(v, Exception):
                    raise v
                return v
        return {"place": None, "neighborhood": None}

    monkeypatch.setattr(geo, "generate_json", fake)
    return replies


live = os.getenv("RUN_LIVE_GEO") == "1"
offline = pytest.mark.skipif(live, reason="offline test")


@offline
def test_normalize():
    assert normalize("  Rancho Peñasquitos!! ") == "rancho penasquitos"
    assert normalize("I'm at UCSD.") == "im at ucsd"


@offline
@pytest.mark.parametrize("text,label", [
    ("La Jolla", "La Jolla"), ("la jolla", "La Jolla"), ("im at ucsd", "UCSD"), ("UC San Diego", "UCSD"),
    ("pb", "Pacific Beach"), ("Rancho Penasquitos", "Rancho Peñasquitos"),
    ("by the sports arena", "Midway District"), ("92103", "92103"),
])
def test_lookup_place(text, label):
    assert lookup_place(text).label == label


@offline
def test_two_letter_alias_only_exact():
    assert lookup_place("pb") is not None
    assert lookup_place("i need help pb") is None  # short aliases don't match inside sentences


@offline
def test_extract_place_skips_gemini_for_zip_and_known_places(gemini):
    gemini["Reply"] = GeminiError("should not be called")
    assert extract_place("im in 92103 rn") == "92103"
    assert extract_place("at my friends place in north park") == "North Park"


@offline
def test_extract_place_uses_gemini_and_adds_neighborhood(gemini):
    gemini["30th"] = {"place": "30th and University", "neighborhood": "North Park"}
    assert extract_place("corner of 30th and university") == "30th and University, North Park"


@offline
def test_extract_place_none_when_no_place(gemini):
    assert extract_place("I'm at home") is None
    assert extract_place("   ") is None


@offline
def test_extract_place_gemini_down_returns_raw(gemini):
    gemini["Petco"] = GeminiError("503")
    assert extract_place("Petco Park") == "Petco Park"


@offline
def test_geocode_known_place_no_nominatim(nominatim):
    assert geocode("La Jolla").label == "La Jolla"
    assert geocode("30th and University, North Park").label == "North Park"
    assert nominatim == []


@offline
def test_geocode_zip_not_in_table_uses_postalcode(nominatim):
    assert geocode("92126") is None
    assert nominatim == [{"postalcode": "92126"}]


@offline
def test_geocode_falls_back_to_nominatim(nominatim):
    assert geocode("Petco Park").label == "Petco Park"
    assert geocode("Los Angeles") is None


@offline
def test_geocode_db_down_still_tries_nominatim(monkeypatch, nominatim):
    monkeypatch.setattr(geo, "_load_places", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    assert geocode("Petco Park").label == "Petco Park"


def test_county_box():
    assert in_san_diego_county(32.7157, -117.1611)       # downtown
    assert in_san_diego_county(33.1217, -117.0815)       # Escondido
    assert not in_san_diego_county(34.0522, -118.2437)   # Los Angeles
    assert not in_san_diego_county(33.8366, -117.9143)   # Anaheim


@pytest.mark.skipif(not live, reason="set RUN_LIVE_GEO=1")
@pytest.mark.parametrize("msg,near", [
    ("La Jolla", (32.85, -117.26)), ("92103", (32.75, -117.17)), ("im at ucsd", (32.88, -117.23)),
    ("Petco Park", (32.71, -117.16)),
])
def test_live_resolves(msg, near):
    p = extract_place(msg)
    g = geocode(p)
    assert g and abs(g.lat - near[0]) < 0.03 and abs(g.lon - near[1]) < 0.03


@pytest.mark.skipif(not live, reason="set RUN_LIVE_GEO=1")
def test_live_los_angeles_is_none():
    assert geocode(extract_place("Los Angeles") or "Los Angeles") is None
