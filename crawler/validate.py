"""Checks on the crawl branch before swapping into main. Empty list means pass.

measure() reads numbers from both databases; problems() decides (pure, unit tested).

Fails the swap:
  * no rows, or more than 30% fewer rows than main (a crawl that broke halfway)
  * any row outside the San Diego County box (bad geocode)
  * any row whose hours don't parse ({"mon": [["08:00","20:00"]]}, HH:MM, open < close)
  * any row with empty accepts or no source_url (would be unusable or unsourced)
  * fewer than 2 rows that can serve each demo coverage (kaiser, chg_medi_cal, uninsured)
Reported but not failing: rows with no hours (never "open now"), uninsured urgent cares with
no self-pay price.
"""
from __future__ import annotations

import re

DAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$|^24:00$")
# San Diego County, generously.
LAT = (32.5, 33.55)
LON = (-117.65, -116.05)
MAX_DROP = 0.30
DEMO_COVERAGE = ("kaiser", "chg_medi_cal", "uninsured")


def hours_ok(hours) -> bool:
    if not isinstance(hours, dict):
        return False
    for day, spans in hours.items():
        if day not in DAYS or not isinstance(spans, list):
            return False
        for span in spans:
            if not (isinstance(span, list) and len(span) == 2 and all(isinstance(t, str) and TIME.match(t) for t in span)):
                return False
            if span[0] >= span[1]:
                return False
    return True


def measure(branch_conn, main_conn) -> dict:
    rows = branch_conn.execute(
        """SELECT id, kind, hours, accepts, source_url, self_pay_price,
                  ST_Y(geom::geometry) AS lat, ST_X(geom::geometry) AS lon
             FROM locations"""
    ).fetchall()
    main_count = main_conn.execute("SELECT count(*) AS n FROM locations").fetchone()["n"]
    return metrics(rows, main_count)


def metrics(rows: list[dict], main_count: int) -> dict:
    outside = [r["id"] for r in rows if not (LAT[0] <= r["lat"] <= LAT[1] and LON[0] <= r["lon"] <= LON[1])]
    bad_hours = [r["id"] for r in rows if not hours_ok(r["hours"])]
    no_hours = [r["id"] for r in rows if r["hours"] == {}]
    no_accepts = [r["id"] for r in rows if not r["accepts"]]
    no_source = [r["id"] for r in rows if not r["source_url"]]
    no_price = [r["id"] for r in rows
                if r["kind"] == "urgent_care" and "uninsured" in (r["accepts"] or []) and r["self_pay_price"] is None]
    coverage = {c: sum(1 for r in rows if c in (r["accepts"] or [])) for c in DEMO_COVERAGE}
    return {
        "row_count": len(rows),
        "main_count": main_count,
        "outside_county": outside,
        "bad_hours": bad_hours,
        "no_hours": no_hours,
        "no_accepts": no_accepts,
        "no_source": no_source,
        "uninsured_urgent_no_price": no_price,
        "coverage": coverage,
    }


def problems(m: dict) -> list[str]:
    out = []
    if m["row_count"] == 0:
        out.append("no rows in the crawl branch")
    elif m["main_count"] and m["row_count"] < (1 - MAX_DROP) * m["main_count"]:
        out.append(f"row count dropped from {m['main_count']} to {m['row_count']} (more than {MAX_DROP:.0%})")
    for key, label in (("outside_county", "outside San Diego County"), ("bad_hours", "with unparseable hours"),
                       ("no_accepts", "with empty accepts"), ("no_source", "with no source_url")):
        if m[key]:
            out.append(f"{len(m[key])} rows {label}: {', '.join(m[key][:5])}")
    for coverage, n in m["coverage"].items():
        if n < 2:
            out.append(f"only {n} rows accept {coverage}")
    return out


def validate(branch_conn, main_conn) -> list[str]:
    return problems(measure(branch_conn, main_conn))
