"""Tests for #2: schema, is_open_now(), seeds, and the HANDOFF section 6 candidate query.

These run against DATABASE_URL (Neon main) and only read. Skipped when it is not set.
"""
import json

import pytest

from app.config import settings

pytestmark = pytest.mark.skipif(not settings.database_url, reason="DATABASE_URL not set")

from app.db import get_conn  # noqa: E402

WEEKDAY_8_TO_8 = {d: [["08:00", "20:00"]] for d in ["mon", "tue", "wed", "thu", "fri", "sat"]}
OVERNIGHT = {"fri": [["22:00", "24:00"]], "sat": [["00:00", "02:00"]]}
ALL_DAY = {d: [["00:00", "24:00"]] for d in ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]}

# 2026-11-18 is a Wednesday, 2026-11-15 a Sunday, 2026-11-20 a Friday. Times are San Diego local.
CASES = [
    (WEEKDAY_8_TO_8, "2026-11-18 10:00", True),   # weekday mid-morning
    (WEEKDAY_8_TO_8, "2026-11-18 08:00", True),   # opening minute counts as open
    (WEEKDAY_8_TO_8, "2026-11-18 20:00", False),  # closing time is exclusive
    (WEEKDAY_8_TO_8, "2026-11-18 07:59", False),
    (WEEKDAY_8_TO_8, "2026-11-15 12:00", False),  # Sunday missing = closed
    (OVERNIGHT, "2026-11-20 23:30", True),        # Friday late
    (OVERNIGHT, "2026-11-21 01:30", True),        # after midnight, Saturday span
    (OVERNIGHT, "2026-11-21 02:30", False),
    (OVERNIGHT, "2026-11-20 21:00", False),
    (ALL_DAY, "2026-11-15 23:59:59", True),
    (ALL_DAY, "2026-11-15 00:00", True),
    ({}, "2026-11-18 10:00", False),               # no hours at all
]


@pytest.mark.parametrize("hours,local_ts,expected", CASES)
def test_is_open_now(hours, local_ts, expected):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT is_open_now(%s::jsonb, (%s::timestamp AT TIME ZONE 'America/Los_Angeles')) AS open",
            (json.dumps(hours), local_ts),
        ).fetchone()
    assert row["open"] is expected


def test_postgis_installed():
    with get_conn() as conn:
        assert conn.execute("SELECT postgis_version() AS v").fetchone()["v"].startswith("3.")


def test_seed_counts_and_coordinates():
    with get_conn() as conn:
        assert conn.execute("SELECT count(*) AS n FROM sd_places").fetchone()["n"] >= 40
        assert conn.execute("SELECT count(*) AS n FROM locations").fetchone()["n"] >= 8
        plans = {r["id"] for r in conn.execute("SELECT id FROM plans").fetchall()}
        assert {"kaiser_cc_silver", "chg_medi_cal"} <= plans
        # Every location sits inside San Diego County's rough bounding box.
        bad = conn.execute(
            """SELECT id FROM locations
                WHERE ST_Y(geom::geometry) NOT BETWEEN 32.5 AND 33.5
                   OR ST_X(geom::geometry) NOT BETWEEN -117.6 AND -116.0"""
        ).fetchall()
        assert bad == []


def test_sd_places_alias_lookup():
    with get_conn() as conn:
        row = conn.execute(
            "SELECT name FROM sd_places WHERE lower(name) = %s OR %s = ANY(aliases)", ("ucsd", "ucsd")
        ).fetchone()
        assert row["name"] == "UCSD"
        assert conn.execute("SELECT 1 AS x FROM sd_places WHERE name = '92103'").fetchone()


# HANDOFF section 6 draft candidate query, with a fixed time so the test does not depend on the clock.
CANDIDATE_SQL = """
SELECT id, kind, ST_Distance(geom, ST_MakePoint(%(lon)s, %(lat)s)::geography) / 1609.34 AS miles
  FROM locations
 WHERE (accepts ? %(plan_id)s OR accepts ? %(coverage_type)s)
   AND is_open_now(hours, (%(at)s::timestamp AT TIME ZONE 'America/Los_Angeles'))
   AND kind = ANY(%(kinds)s)
   AND (NOT %(needs_xray)s OR has_xray)
   AND ST_DWithin(geom, ST_MakePoint(%(lon)s, %(lat)s)::geography, %(radius_m)s)
 ORDER BY miles
 LIMIT 10
"""

UCSD = {"lat": 32.879244, "lon": -117.231125}
WED_1PM = "2026-11-18 13:00"
FIFTEEN_MILES_M = 15 * 1609.34


def _candidates(plan_id, coverage_type, kinds, needs_xray=False, at=WED_1PM):
    with get_conn() as conn:
        return conn.execute(
            CANDIDATE_SQL,
            {**UCSD, "plan_id": plan_id, "coverage_type": coverage_type, "kinds": kinds,
             "needs_xray": needs_xray, "radius_m": FIFTEEN_MILES_M, "at": at},
        ).fetchall()


def test_candidates_kaiser_ankle_near_ucsd():
    rows = _candidates("kaiser_cc_silver", "kaiser", ["kaiser"], needs_xray=True)
    assert [r["id"] for r in rows] == ["kaiser_sdmc_urgent"]


def test_candidates_medi_cal_ankle_near_ucsd():
    rows = _candidates("chg_medi_cal", "medi_cal", ["urgent_care", "fqhc"])
    ids = {r["id"] for r in rows}
    # The #11 import adds dozens of CHG sites, so the 10 nearest no longer include a fixed seed.
    assert "urgent_care_and_more_midway" in ids
    assert {r["kind"] for r in rows} == {"urgent_care", "fqhc"}
    with get_conn() as conn:
        accepts = conn.execute("SELECT id, accepts FROM locations WHERE id = ANY(%s)", (list(ids),)).fetchall()
    assert all("chg_medi_cal" in r["accepts"] for r in accepts)  # CHG network only


def test_candidates_uninsured_ankle_near_ucsd():
    ids = {r["id"] for r in _candidates(None, "uninsured", ["urgent_care", "fqhc"])}
    assert {"afc_mira_mesa", "afc_clairemont", "fhcsd_logan_heights"} <= ids
    assert "kaiser_sdmc_urgent" not in ids and "urgent_care_and_more_midway" not in ids


def test_candidates_closed_locations_excluded():
    # Sunday 3pm: FHCSD is closed Sundays and the Midway clinic closes at 2pm.
    ids = {r["id"] for r in _candidates("chg_medi_cal", "medi_cal", ["urgent_care", "fqhc"], at="2026-11-15 15:00")}
    assert not ids & {"fhcsd_logan_heights", "urgent_care_and_more_midway"}
    # Sunday 11:30pm: no urgent care or clinic is open.
    assert _candidates("chg_medi_cal", "medi_cal", ["urgent_care", "fqhc"], at="2026-11-15 23:30") == []
