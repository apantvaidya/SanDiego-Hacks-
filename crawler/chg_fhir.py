"""CHG Medi-Cal network from its public FHIR provider directory API (no login). Owned by #11.

Base: https://api-chgsd-prd.safhir.io/v1/api/provider-directory/  (Da Vinci Plan-Net)
Rate limit: 1 request per second (the API answers 429 "throttled" faster than that).

What we pull: HealthcareService?service-category=urg (CHG's in-network urgent care
centers, about 48), then each one's Location for address, phone and hours. Found by
exploring the API on 2026-11-15:
  * primary care clinics are listed per practitioner (PractitionerRole), not as services,
    so they are not pulled here; FHCSD Logan Heights is already seeded with chg_medi_cal.
  * search "next" links return 403, so we query by category instead of paging everything.
  * Locations have no coordinates; addresses are geocoded with Nominatim (1 req/s) and
    rows outside San Diego County or not found are skipped, never placed at a ZIP centroid.

Writing (never at message time; CLAUDE.md "cached in Neon"):
  * a location already in the table at the same street address gets 'chg_medi_cal'
    added to accepts (nothing else changes)
  * new sites are inserted as kind 'urgent_care', id 'chg_<number>', accepts
    ['chg_medi_cal'], has_xray / walk_in / prices NULL (the directory does not say)
  * additive only: no deletes, safe to re-run

    uv run python -m crawler.chg_fhir            # fetch + geocode, print, write nothing
    uv run python -m crawler.chg_fhir --apply    # also write to DATABASE_URL (Neon main)
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from datetime import datetime, timezone

import httpx

from app.geo.geocode import _nominatim, in_san_diego_county

log = logging.getLogger(__name__)

BASE = "https://api-chgsd-prd.safhir.io/v1/api/provider-directory"
MIN_INTERVAL_S = 1.2
USER_AGENT = "care-navigator-hackathon"
PLAN_ID = "chg_medi_cal"
_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

_last_call = 0.0


def _get(path: str, params: dict | None = None) -> dict:
    """GET one FHIR resource or search, at most 1 request per second, retrying throttles."""
    global _last_call
    last_error: Exception | str | None = None
    for attempt in range(5):
        wait = MIN_INTERVAL_S - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        try:
            resp = httpx.get(f"{BASE}/{path}", params=params, timeout=60,
                             headers={"User-Agent": USER_AGENT, "Accept": "application/fhir+json"})
            _last_call = time.monotonic()
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            _last_call = time.monotonic()
            last_error = e
            time.sleep(2 * (attempt + 1))
            continue
        if data.get("resourceType") == "OperationOutcome":
            last_error = json.dumps(data.get("issue"))[:200]
            time.sleep(2 * (attempt + 1))
            continue
        return data
    raise RuntimeError(f"CHG FHIR {path} failed: {last_error}")


# --------------------------------------------------------------------------- parsing

def _hhmm(t: str) -> str:
    return t[:5]


def parse_hours(hours_of_operation: list[dict] | None) -> dict:
    """FHIR hoursOfOperation -> {"mon": [["08:00","20:00"]], ...} in our [open, close) format.
    allDay -> 00:00-24:00; a 00:00 close -> 24:00; a close before the open is split across
    midnight into the next day."""
    out: dict[str, list[list[str]]] = {}
    for h in hours_of_operation or []:
        days = [d for d in h.get("daysOfWeek", []) if d in _DAYS]
        if h.get("allDay"):
            spans_by_day = {d: [["00:00", "24:00"]] for d in days}
        else:
            if not h.get("openingTime") or not h.get("closingTime"):
                continue
            open_t, close_t = _hhmm(h["openingTime"]), _hhmm(h["closingTime"])
            spans_by_day = {}
            for d in days:
                if close_t == "00:00":
                    spans_by_day.setdefault(d, []).append([open_t, "24:00"])
                elif close_t <= open_t:
                    nxt = _DAYS[(_DAYS.index(d) + 1) % 7]
                    spans_by_day.setdefault(d, []).append([open_t, "24:00"])
                    spans_by_day.setdefault(nxt, []).append(["00:00", close_t])
                else:
                    spans_by_day.setdefault(d, []).append([open_t, close_t])
        for d, spans in spans_by_day.items():
            for span in spans:
                if span not in out.setdefault(d, []):
                    out[d].append(span)
    return {d: sorted(out[d]) for d in _DAYS if d in out}


def _phone(telecom: list[dict] | None) -> str | None:
    phones = sorted((t for t in telecom or [] if t.get("system") == "phone"), key=lambda t: t.get("rank", 99))
    return phones[0]["value"] if phones else None


def _hours_from(loc: dict) -> dict:
    """Location.hoursOfOperation, else the plan-net availableTime extension on the main phone."""
    hours = parse_hours(loc.get("hoursOfOperation"))
    if hours:
        return hours
    for t in sorted(loc.get("telecom") or [], key=lambda t: t.get("rank", 99)):
        spans = []
        for ext in t.get("extension", []):
            if not ext.get("url", "").endswith("contactpoint-availabletime"):
                continue
            item: dict = {"daysOfWeek": []}
            for sub in ext.get("extension", []):
                key, val = sub.get("url"), next((v for k, v in sub.items() if k.startswith("value")), None)
                if key == "daysOfWeek":
                    item["daysOfWeek"].append(val)
                elif key == "allDay":
                    item["allDay"] = val
                elif key == "availableStartTime":
                    item["openingTime"] = val
                elif key == "availableEndTime":
                    item["closingTime"] = val
            spans.append(item)
        if spans:
            return parse_hours(spans)
    return {}


def _address(loc: dict) -> tuple[str, str | None]:
    a = loc.get("address") or {}
    line = " ".join(a.get("line") or [])
    city, state, zip_ = a.get("city"), a.get("state") or "CA", (a.get("postalCode") or "")[:5]
    return f"{line}, {city}, {state} {zip_}".strip(", "), zip_ or None


def _street_for_geocoding(full: str) -> str:
    # Nominatim does not know suite numbers: "3434 Midway Dr Ste 1002" -> "3434 Midway Dr"
    return re.sub(r"\s+(ste|suite|unit|#|bldg|building|apt|rm|room)\.?\s*[\w-]+", "", full, flags=re.IGNORECASE)


def address_key(address: str) -> str:
    """Street number + first street word + ZIP, lowercase: matches '3434 Midway Dr Ste 1002,
    San Diego, CA 92110' with '3434 Midway Drive, San Diego, CA 92110'."""
    a = address.lower()
    m = re.match(r"\s*(\d+)\s+([a-z0-9]+)", a)
    z = re.search(r"\b(9[12]\d{3})\b", a)
    return f"{m.group(1)} {m.group(2)} {z.group(1) if z else ''}".strip() if m else a


def stable_id(fhir_id: str) -> str:
    """'CHG5064112' -> 'chg_5064112'. Stable across re-imports."""
    return "chg_" + re.sub(r"^chg", "", fhir_id.lower())


def _title(name: str) -> str:
    return name.title() if name.isupper() else name


# --------------------------------------------------------------------------- public

def fetch_chg_locations(geocode: bool = True) -> list[dict]:
    """CHG in-network urgent care sites as `locations` rows (dicts with the table's columns,
    plus lat/lon). Skips sites that cannot be geocoded inside San Diego County."""
    services = _get("HealthcareService", {"service-category": "urg", "_count": 500}).get("entry", [])
    loc_ids = []
    for e in services:
        for ref in e["resource"].get("location", []):
            lid = ref.get("reference", "").split("/")[-1]
            if lid and lid not in loc_ids:
                loc_ids.append(lid)
    log.info("CHG: %d urgent care services, %d locations", len(services), len(loc_ids))

    now = datetime.now(timezone.utc)
    rows, seen_keys = [], set()
    for lid in loc_ids:
        loc = _get(f"Location/{lid}")
        address, _zip = _address(loc)
        key = address_key(address)
        if key in seen_keys:  # same site listed under several ids
            continue
        seen_keys.add(key)
        row = {
            "id": stable_id(lid), "fhir_id": lid, "name": _title(loc.get("name") or "Urgent Care"),
            "kind": "urgent_care", "address": address, "phone": _phone(loc.get("telecom")),
            "hours": _hours_from(loc), "has_xray": None, "walk_in": None, "accepts": [PLAN_ID],
            "self_pay_price": None, "sliding_scale": None,
            "source_url": f"{BASE}/Location/{lid}", "crawled_at": now, "lat": None, "lon": None,
        }
        if geocode:
            point = _nominatim({"q": _street_for_geocoding(address)})
            if point is None or not in_san_diego_county(point.lat, point.lon):
                log.warning("CHG: could not place %s (%s); skipped", row["name"], address)
                continue
            row["lat"], row["lon"] = point.lat, point.lon
        rows.append(row)
    return rows


def apply(rows: list[dict], url: str | None = None) -> dict:
    """Write rows to Neon (main unless url is a branch). Existing site at the same address:
    add chg_medi_cal to accepts. New site: insert. Returns counts."""
    from psycopg.types.json import Jsonb

    from app.db import get_conn

    counts = {"matched": 0, "inserted": 0, "updated": 0}
    with get_conn(url) as conn:
        existing = conn.execute("SELECT id, address, accepts FROM locations").fetchall()
        by_key = {address_key(r["address"]): r for r in existing}
        for row in rows:
            match = by_key.get(address_key(row["address"]))
            if match and match["id"] != row["id"]:
                if PLAN_ID not in (match["accepts"] or []):
                    conn.execute("UPDATE locations SET accepts = accepts || %s::jsonb WHERE id = %s",
                                 (Jsonb([PLAN_ID]), match["id"]))
                counts["matched"] += 1
                continue
            conn.execute(
                """INSERT INTO locations (id, name, kind, geom, address, phone, hours, has_xray, walk_in,
                                          accepts, self_pay_price, sliding_scale, source_url, crawled_at)
                   VALUES (%(id)s, %(name)s, %(kind)s,
                           ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography,
                           %(address)s, %(phone)s, %(hours)s, NULL, NULL, %(accepts)s, NULL, NULL,
                           %(source_url)s, %(crawled_at)s)
                   ON CONFLICT (id) DO UPDATE SET
                     name = EXCLUDED.name, geom = EXCLUDED.geom, address = EXCLUDED.address,
                     phone = EXCLUDED.phone, hours = EXCLUDED.hours, source_url = EXCLUDED.source_url,
                     crawled_at = EXCLUDED.crawled_at,
                     accepts = (SELECT jsonb_agg(DISTINCT v) FROM jsonb_array_elements(locations.accepts || EXCLUDED.accepts) v)""",
                {**row, "hours": Jsonb(row["hours"]), "accepts": Jsonb(row["accepts"])},
            )
            counts["updated" if row["id"] in {r["id"] for r in existing} else "inserted"] += 1
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--apply", action="store_true", help="write to DATABASE_URL")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    rows = fetch_chg_locations()
    for r in rows:
        days = ",".join(d for d in _DAYS if d in r["hours"]) or "no hours"
        print(f"{r['id']:16} {r['name'][:40]:40} {r['address'][:48]:48} {days}")
    print(f"{len(rows)} sites placed in San Diego County")
    if args.apply:
        print(apply(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
