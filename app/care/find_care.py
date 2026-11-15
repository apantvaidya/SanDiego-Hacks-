"""Candidate query: one PostGIS query in Neon. Owned by #6.

find_candidates() filters, in SQL:
  * coverage: accepts ? plan_id OR accepts ? coverage_type
  * open now: is_open_now(hours, at) (db/schema.sql)
  * kind allowed for the level of care (KINDS_FOR_LEVEL); Kaiser members only get Kaiser
    sites, except ERs at the ER level
  * walk-in (urgent care level only): FQHCs must be known walk-in; other kinds just not
    known "no". Primary care can be by appointment.
  * X-ray: when needs_xray, drop places known NOT to have one. Unknown (NULL) stays in,
    so render can say "call ahead about X-ray" rather than hide the option.
  * distance: ST_DWithin(radius), ordered nearest first

find_candidates_widening() runs it at 5 miles, then once at 15 miles if empty (HANDOFF 3).
"""
from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from app.db import get_conn
from app.models import CareLevel, CoverageType, GeoPoint, Location, Profile

SD_TZ = ZoneInfo("America/Los_Angeles")
METERS_PER_MILE = 1609.34
WIDE_RADIUS_MILES = 15.0

# Location kinds that can handle each level of care. Order does not matter (SQL sorts by distance).
KINDS_FOR_LEVEL: dict[CareLevel, list[str]] = {
    CareLevel.SELF_CARE: ["telehealth", "fqhc", "kaiser"],     # only if they want to be seen anyway
    CareLevel.TELEHEALTH: ["telehealth", "fqhc", "kaiser"],
    CareLevel.PRIMARY_CARE: ["fqhc", "kaiser"],
    CareLevel.URGENT_CARE: ["urgent_care", "fqhc", "kaiser"],
    CareLevel.ER: ["er"],
}

_SQL = """
SELECT id, name, kind, address, phone, hours, has_xray, walk_in, accepts,
       self_pay_price, sliding_scale, source_url, crawled_at,
       ST_Y(geom::geometry) AS lat, ST_X(geom::geometry) AS lon,
       ST_Distance(geom, ST_MakePoint(%(lon)s, %(lat)s)::geography) / %(m_per_mile)s AS miles
  FROM locations
 WHERE ((%(plan_id)s::text IS NOT NULL AND accepts ? %(plan_id)s::text) OR accepts ? %(coverage_type)s)
   AND is_open_now(hours, %(at)s)
   AND kind = ANY(%(kinds)s)
   AND (NOT %(urgent)s OR (walk_in IS NOT FALSE AND (kind <> 'fqhc' OR walk_in IS TRUE)))
   AND (NOT %(needs_xray)s OR has_xray IS NOT FALSE)
   AND ST_DWithin(geom, ST_MakePoint(%(lon)s, %(lat)s)::geography, %(radius_m)s)
 ORDER BY miles
 LIMIT %(limit)s
"""


def kinds_for(profile: Profile, level: CareLevel) -> list[str]:
    kinds = KINDS_FOR_LEVEL[level]
    if _coverage(profile) == CoverageType.KAISER.value and level is not CareLevel.ER:
        kinds = [k for k in kinds if k in ("kaiser", "telehealth")]
    return kinds


def _coverage(profile: Profile) -> str:
    ct = profile.coverage_type
    return ct.value if isinstance(ct, CoverageType) else str(ct)


def find_candidates(profile: Profile, level: CareLevel, point: GeoPoint,
                    radius_miles: float = 5, limit: int = 10, *,
                    needs_xray: bool = False, at: datetime | None = None) -> list[Location]:
    """Open, in-network, capable locations within radius_miles, nearest first.

    needs_xray and at are keyword-only extras: `at` pins the clock for tests and demos.
    """
    params = {
        "lat": point.lat, "lon": point.lon, "m_per_mile": METERS_PER_MILE,
        "plan_id": profile.plan_id, "coverage_type": _coverage(profile),
        "at": at or datetime.now(SD_TZ),
        "kinds": kinds_for(profile, level),
        "urgent": level is CareLevel.URGENT_CARE,
        "needs_xray": needs_xray,
        "radius_m": radius_miles * METERS_PER_MILE,
        "limit": limit,
    }
    with get_conn() as conn:
        rows = conn.execute(_SQL, params).fetchall()
    return [_to_location(r) for r in rows]


def find_candidates_widening(profile: Profile, level: CareLevel, point: GeoPoint,
                             radius_miles: float = 5, limit: int = 10, *,
                             needs_xray: bool = False, at: datetime | None = None,
                             wide_radius_miles: float = WIDE_RADIUS_MILES) -> tuple[list[Location], float]:
    """Try radius_miles, then widen once. Returns (candidates, radius used). Empty means
    the caller sends the honest fallback (#7 render_fallback)."""
    found = find_candidates(profile, level, point, radius_miles, limit, needs_xray=needs_xray, at=at)
    if found or wide_radius_miles <= radius_miles:
        return found, radius_miles
    return find_candidates(profile, level, point, wide_radius_miles, limit,
                           needs_xray=needs_xray, at=at), wide_radius_miles


def _to_location(r: dict) -> Location:
    return Location(
        id=r["id"], name=r["name"], kind=r["kind"], lat=r["lat"], lon=r["lon"],
        address=r["address"], phone=r["phone"], hours=r["hours"] or {},
        has_xray=r["has_xray"], walk_in=r["walk_in"], accepts=list(r["accepts"] or []),
        self_pay_price=float(r["self_pay_price"]) if r["self_pay_price"] is not None else None,
        sliding_scale=r["sliding_scale"], source_url=r["source_url"], crawled_at=r["crawled_at"],
        distance_miles=round(float(r["miles"]), 1),
    )


# --------------------------------------------------------------------------- hours helpers

_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _parse(t: str) -> tuple[int, int]:
    h, m = t.split(":")[:2]
    return int(h), int(m)


def _minutes(t: str) -> int:
    h, m = _parse(t)
    return h * 60 + m


def is_open_now(hours: dict, at: datetime | None = None) -> bool:
    """Pure helper, America/Los_Angeles time. Mirrors the SQL is_open_now(): spans are
    [open, close), "24:00" means midnight at the end of the day, missing day means closed."""
    now = (at or datetime.now(SD_TZ)).astimezone(SD_TZ)
    minute = now.hour * 60 + now.minute + now.second / 60
    for open_t, close_t in (hours or {}).get(_DAYS[now.weekday()], []):
        if _minutes(open_t) <= minute < _minutes(close_t):
            return True
    return False


def closes_at(hours: dict, at: datetime | None = None) -> time | None:
    """Closing time of the span open right now (for "open until 8 PM"), or None if closed.
    A span ending "24:00" returns time(23, 59)."""
    now = (at or datetime.now(SD_TZ)).astimezone(SD_TZ)
    minute = now.hour * 60 + now.minute + now.second / 60
    for open_t, close_t in (hours or {}).get(_DAYS[now.weekday()], []):
        if _minutes(open_t) <= minute < _minutes(close_t):
            h, m = _parse(close_t)
            return time(23, 59) if h == 24 else time(h, m)
    return None
