"""Tests for #6: find_candidates against the #2 seeds on Neon (read-only), plus the pure
is_open_now / closes_at helpers and their parity with the SQL function."""
import json
import uuid
from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.care.find_care import (
    closes_at, find_candidates, find_candidates_widening, is_open_now, kinds_for,
)
from app.config import settings
from app.models import CareLevel, CoverageType, FplRange, GeoPoint, Profile

SD = ZoneInfo("America/Los_Angeles")
UCSD = GeoPoint(lat=32.879244, lon=-117.231125, label="UCSD")
WED_1PM = datetime(2026, 11, 18, 13, 0, tzinfo=SD)
SUN_3PM = datetime(2026, 11, 15, 15, 0, tzinfo=SD)

needs_db = pytest.mark.skipif(not settings.database_url, reason="DATABASE_URL not set")


def _p(coverage, plan_id=None, income=None):
    return Profile(id=uuid.uuid4(), user_id=uuid.uuid4(), label=coverage.value, coverage_type=coverage,
                   plan_id=plan_id, income_range_fpl=income)


KAISER = _p(CoverageType.KAISER, "kaiser_cc_silver")
MEDI_CAL = _p(CoverageType.MEDI_CAL, "chg_medi_cal")
UNINSURED = _p(CoverageType.UNINSURED, income=FplRange.UNDER_138)


# --------------------------------------------------------------------------- pure helpers

WEEKDAY = {d: [["08:00", "20:00"]] for d in ["mon", "tue", "wed", "thu", "fri", "sat"]}
SPLIT = {"wed": [["08:00", "12:00"], ["13:00", "17:00"]]}
LATE = {"fri": [["22:00", "24:00"]], "sat": [["00:00", "02:00"]]}


@pytest.mark.parametrize("hours,at,expected", [
    (WEEKDAY, datetime(2026, 11, 18, 10, 0, tzinfo=SD), True),
    (WEEKDAY, datetime(2026, 11, 18, 20, 0, tzinfo=SD), False),
    (WEEKDAY, datetime(2026, 11, 15, 12, 0, tzinfo=SD), False),       # Sunday
    (SPLIT, datetime(2026, 11, 18, 12, 30, tzinfo=SD), False),        # lunch gap
    (SPLIT, datetime(2026, 11, 18, 13, 0, tzinfo=SD), True),
    (LATE, datetime(2026, 11, 20, 23, 59, 30, tzinfo=SD), True),
    (LATE, datetime(2026, 11, 21, 1, 0, tzinfo=SD), True),
    (WEEKDAY, datetime(2026, 11, 18, 18, 0, tzinfo=ZoneInfo("UTC")), True),  # 10:00 in San Diego
    ({}, datetime(2026, 11, 18, 10, 0, tzinfo=SD), False),
])
def test_is_open_now_python(hours, at, expected):
    assert is_open_now(hours, at) is expected


@needs_db
@pytest.mark.parametrize("hours,at", [
    (WEEKDAY, datetime(2026, 11, 18, 10, 0, tzinfo=SD)),
    (WEEKDAY, datetime(2026, 11, 18, 20, 0, tzinfo=SD)),
    (SPLIT, datetime(2026, 11, 18, 12, 30, tzinfo=SD)),
    (LATE, datetime(2026, 11, 21, 1, 0, tzinfo=SD)),
    (LATE, datetime(2026, 11, 20, 23, 59, 30, tzinfo=SD)),
])
def test_python_matches_sql(hours, at):
    from app.db import get_conn
    with get_conn() as conn:
        sql = conn.execute("SELECT is_open_now(%s::jsonb, %s) AS o", (json.dumps(hours), at)).fetchone()["o"]
    assert sql is is_open_now(hours, at)


def test_closes_at():
    assert closes_at(WEEKDAY, datetime(2026, 11, 18, 10, 0, tzinfo=SD)) == time(20, 0)
    assert closes_at(LATE, datetime(2026, 11, 20, 23, 0, tzinfo=SD)) == time(23, 59)
    assert closes_at(WEEKDAY, datetime(2026, 11, 15, 10, 0, tzinfo=SD)) is None


def test_kinds_for_kaiser_only_kaiser_except_er():
    assert kinds_for(KAISER, CareLevel.URGENT_CARE) == ["kaiser"]
    assert kinds_for(KAISER, CareLevel.ER) == ["er"]
    assert "urgent_care" in kinds_for(UNINSURED, CareLevel.URGENT_CARE)


# --------------------------------------------------------------------------- against the seeds

@needs_db
def test_kaiser_ankle_near_ucsd_widens_to_kaiser_urgent_care():
    assert find_candidates(KAISER, CareLevel.URGENT_CARE, UCSD, 5, needs_xray=True, at=WED_1PM) == []
    found, radius = find_candidates_widening(KAISER, CareLevel.URGENT_CARE, UCSD, needs_xray=True, at=WED_1PM)
    assert radius == 15
    assert [loc.id for loc in found] == ["kaiser_sdmc_urgent"]
    assert 5 < found[0].distance_miles < 10


@needs_db
def test_medi_cal_ankle_near_ucsd_gets_chg_sites_only():
    found, _ = find_candidates_widening(MEDI_CAL, CareLevel.URGENT_CARE, UCSD, needs_xray=True, at=WED_1PM)
    ids = [loc.id for loc in found]
    assert set(ids) == {"urgent_care_and_more_midway", "fhcsd_logan_heights"}  # unknown X-ray kept
    assert ids == sorted(ids, key=lambda i: {l.id: l.distance_miles for l in found}[i])  # nearest first


@needs_db
def test_uninsured_ankle_near_ucsd_gets_cash_and_sliding_scale():
    found, _ = find_candidates_widening(UNINSURED, CareLevel.URGENT_CARE, UCSD, needs_xray=True, at=WED_1PM)
    ids = {loc.id for loc in found}
    assert {"afc_mira_mesa", "afc_clairemont", "fhcsd_logan_heights"} <= ids
    assert not ids & {"kaiser_sdmc_urgent", "urgent_care_and_more_midway", "ucsd_jacobs_er"}
    afc = next(l for l in found if l.id == "afc_mira_mesa")
    assert afc.self_pay_price == 199 and afc.has_xray is True and afc.lat and afc.lon


@needs_db
def test_er_level_returns_nearest_er_for_everyone():
    for p in (KAISER, MEDI_CAL, UNINSURED):
        found = find_candidates(p, CareLevel.ER, UCSD, 5, at=WED_1PM)
        assert found[0].id == "ucsd_jacobs_er"


@needs_db
def test_closed_locations_excluded_sunday_afternoon():
    found, radius = find_candidates_widening(MEDI_CAL, CareLevel.URGENT_CARE, UCSD, at=SUN_3PM)
    assert found == [] and radius == 15


@needs_db
def test_limit_respected():
    assert len(find_candidates(UNINSURED, CareLevel.URGENT_CARE, UCSD, 15, limit=1, at=WED_1PM)) == 1
