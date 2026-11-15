"""CHG Medi-Cal network from its public FHIR provider directory API (no login).

    uv run python -m crawler.chg_fhir            # fetch + geocode, print, write nothing
    uv run python -m crawler.chg_fhir --apply    # also write to Neon main (or --url <branch>)

API facts (checked Nov 15, 2026):
- Base https://api-chgsd-prd.safhir.io/v1/api/provider-directory, FHIR R4, Da Vinci Plan-Net 1.2.0.
- Rate limit: 1 request per second (HTTP 429 / "throttled" with Retry-After).
- Three networks: Medi-Cal (Organization/MCALNTWK) and two Medicare plans. HealthcareService and
  affiliation IDs carry the network in their prefix: CHGM = Medi-Cal, CHGC / CHGD = Medicare plans.
- Hours live on HealthcareService.telecom as Plan-Net contactpoint-availabletime extensions.
- Location.position is empty, so we geocode addresses (Nominatim, 1 request per second). A street
  result must land within 8 km of the ZIP's center; sites we can't place are skipped, never put
  at a ZIP centroid (distances shown to users must be real).
- There is no FQHC service category. Community health centers are provider groups
  (Organization type prvgrp), so we look them up by name and take their Medi-Cal sites.

What gets loaded:
- urgent_care: HealthcareService category "urg" in the Medi-Cal network (walk_in = true).
- lab: HealthcareService category "lab" (clinical laboratory) in the Medi-Cal network.
- fqhc: Medi-Cal sites of the community health centers in FQHC_ORGS.
Only San Diego County ZIPs (919xx to 921xx) are kept. Staff emails in the directory are ignored.

Writes: rows with stable ids `chg_<location number>` (e.g. chg_5064112) and accepts ["chg_medi_cal"]. If an
existing location (for example a hand seed) is within 75 m, we add "chg_medi_cal" to its
accepts instead of inserting a duplicate. The responses are cached in crawler/raw/chg/ so a
re-run doesn't hit the API again (delete the folder to refresh).

Crawl swaps (#13) replace the locations table from a branch: run this loader against the crawl
branch too (load(conn) takes any connection), or the CHG rows will be dropped by the swap.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from psycopg.types.json import Jsonb

from app.config import settings

log = logging.getLogger(__name__)

BASE = "https://api-chgsd-prd.safhir.io/v1/api/provider-directory"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
CACHE = Path("crawler/raw/chg")
PLAN_ID = "chg_medi_cal"
MEDI_CAL_PREFIX = "CHGM"
AVAILABLE_TIME = "contactpoint-availabletime"
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MATCH_METERS = 75

# San Diego County community health centers (FQHCs and look-alikes) to look up by name.
FQHC_ORGS = [
    "Family Health Centers of San Diego",
    "San Ysidro Health",
    "La Maestra",
    "Neighborhood Healthcare",
    "TrueCare",
    "Vista Community Clinic",
    "Mountain Health",
    "San Diego American Indian Health",
    "Imperial Beach Health Center",
]


# ---------------------------------------------------------------------------
# HTTP with rate limiting and a disk cache
# ---------------------------------------------------------------------------

class _Throttle:
    def __init__(self, interval: float):
        self.interval = interval
        self.last = 0.0

    def wait(self) -> None:
        gap = time.monotonic() - self.last
        if gap < self.interval:
            time.sleep(self.interval - gap)
        self.last = time.monotonic()


_fhir_throttle = _Throttle(1.1)
_geo_throttle = _Throttle(1.1)


def _cache_path(url: str) -> Path:
    return CACHE / (hashlib.sha1(url.encode()).hexdigest() + ".json")


def fhir_get(url: str, client: httpx.Client) -> dict:
    """GET a FHIR URL (absolute or relative to BASE), cached on disk, 1 request per second."""
    if not url.startswith("http"):
        url = f"{BASE}/{url}"
    path = _cache_path(url)
    if path.exists():
        return json.loads(path.read_text())
    for attempt in range(5):
        _fhir_throttle.wait()
        resp = client.get(url, headers={"Accept": "application/fhir+json"})
        throttled = resp.status_code == 429 or '"throttled"' in resp.text[:500]
        if throttled:
            time.sleep(float(resp.headers.get("Retry-After", 2)))
            continue
        resp.raise_for_status()
        data = resp.json()
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        return data
    raise RuntimeError(f"CHG API kept throttling: {url}")


def fhir_search(query: str, client: httpx.Client, max_pages: int = 20) -> list[dict]:
    """All resources for a search, following `next` links."""
    out: list[dict] = []
    url: str | None = query
    for _ in range(max_pages):
        if not url:
            break
        try:
            bundle = fhir_get(url, client)
        except httpx.HTTPStatusError as exc:
            # Continuation links sometimes return 403. Keep the pages we already have.
            if not out:
                raise
            log.warning("stopped paging after %s results: %s", len(out), exc.response.status_code)
            break
        out.extend(e["resource"] for e in bundle.get("entry", []))
        url = next((link["url"] for link in bundle.get("link", []) if link["relation"] == "next"), None)
    return out


# ---------------------------------------------------------------------------
# Parsing (pure, unit tested)
# ---------------------------------------------------------------------------

def _hhmm(value: str) -> str:
    return value[:5]


def _add_span(hours: dict, day: str, start: str, end: str) -> None:
    if end == "00:00":
        end = "24:00"
    if end <= start and end != "24:00":
        # Overnight: split across two days.
        nxt = DAYS[(DAYS.index(day) + 1) % 7]
        hours.setdefault(day, []).append([start, "24:00"])
        hours.setdefault(nxt, []).append(["00:00", end])
    else:
        hours.setdefault(day, []).append([start, end])


def clean_all_day(hours: dict, allow_all_day: bool = True) -> dict:
    """Directory records often attach a 24/7 phone line (nurse line, answering service) next to
    the real clinic hours. Drop an all-day span when the same day has real hours, and drop all
    all-day spans when the site type is never open 24/7 (clinics, labs)."""
    out = {}
    for day, spans in hours.items():
        real = [s for s in spans if s != ["00:00", "24:00"]]
        keep = real if (real or not allow_all_day) else spans
        if keep:
            out[day] = keep
    return out


def hours_from_available_time(telecoms: list[dict]) -> dict:
    """Plan-Net contactpoint-availabletime extensions -> {"mon": [["08:00","20:00"]], ...}."""
    hours: dict[str, list[list[str]]] = {}
    seen: set[tuple] = set()
    for telecom in telecoms or []:
        for ext in telecom.get("extension", []):
            if not ext.get("url", "").endswith(AVAILABLE_TIME):
                continue
            days, all_day, start, end = [], False, None, None
            for part in ext.get("extension", []):
                key = part.get("url")
                if key == "daysOfWeek":
                    days.append(part["valueCode"])
                elif key == "allDay":
                    all_day = bool(part.get("valueBoolean"))
                elif key == "availableStartTime":
                    start = _hhmm(part["valueTime"])
                elif key == "availableEndTime":
                    end = _hhmm(part["valueTime"])
            for day in days:
                span = ("00:00", "24:00") if all_day else (start, end)
                if day not in DAYS or None in span or (day, span) in seen:
                    continue
                seen.add((day, span))
                _add_span(hours, day, *span)
    return {d: sorted(hours[d]) for d in DAYS if d in hours}


def hours_from_location(location: dict) -> dict:
    """Standard FHIR Location.hoursOfOperation -> our hours format."""
    hours: dict[str, list[list[str]]] = {}
    for block in location.get("hoursOfOperation", []) or []:
        span = ("00:00", "24:00") if block.get("allDay") else (
            _hhmm(block.get("openingTime", "")), _hhmm(block.get("closingTime", ""))
        )
        if not all(span):
            continue
        for day in block.get("daysOfWeek", []):
            if day in DAYS:
                _add_span(hours, day, *span)
    return {d: sorted(hours[d]) for d in DAYS if d in hours}


def is_medi_cal(resource_id: str) -> bool:
    return resource_id.startswith(MEDI_CAL_PREFIX)


def in_san_diego_county(postal_code: str | None) -> bool:
    return bool(postal_code) and bool(re.match(r"^9(19|20|21)\d{2}", postal_code))


def phone_from(telecoms: list[dict]) -> str | None:
    for t in telecoms or []:
        if t.get("system") == "phone" and t.get("value"):
            return t["value"]
    return None


def address_text(location: dict) -> str:
    a = location.get("address") or {}
    if a.get("line"):
        parts = [", ".join(a["line"]), a.get("city"), f"{a.get('state', '')} {a.get('postalCode', '')}".strip()]
        return ", ".join(p for p in parts if p)
    return (a.get("text") or "").replace(", US", "")


def building_key(kind: str, address: str) -> tuple[str, str]:
    """('fqhc', '4094 Fourth Ave, First Floor, San Diego, CA 92103') -> ('fqhc', '4094 fourth ave 92103')."""
    street = re.sub(r"\W+", " ", address.split(",")[0].lower()).strip()
    zip_match = re.search(r"\b(9\d{4})\b", address)
    return kind, f"{street} {zip_match.group(1) if zip_match else ''}".strip()


def stable_id(fhir_id: str) -> str:
    """'CHG5064112' or 'Location/CHG5064112' -> 'chg_5064112'. Stable across re-imports."""
    return "chg_" + re.sub(r"\D", "", fhir_id.split("/")[-1])


def row_id(kind: str, location_ref: str) -> str:
    """One row per CHG Location; the first kind seen wins (urgent care, then lab, then clinic)."""
    return stable_id(location_ref)


# ---------------------------------------------------------------------------
# Geocoding
# ---------------------------------------------------------------------------

def _nominatim(params: dict, client: httpx.Client) -> tuple[float, float] | None:
    params = {**params, "format": "json", "limit": 1, "countrycodes": "us"}
    path = _cache_path("geo:" + json.dumps(params, sort_keys=True))
    if path.exists():
        result = json.loads(path.read_text())
    else:
        _geo_throttle.wait()
        resp = client.get(NOMINATIM, params=params, headers={"User-Agent": settings.nominatim_user_agent})
        resp.raise_for_status()
        result = resp.json()
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result))
    return (float(result[0]["lat"]), float(result[0]["lon"])) if result else None


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


MAX_KM_FROM_ZIP = 8.0

_ORDINALS = {
    "first": "1st", "second": "2nd", "third": "3rd", "fourth": "4th", "fifth": "5th", "sixth": "6th",
    "seventh": "7th", "eighth": "8th", "ninth": "9th", "tenth": "10th", "eleventh": "11th", "twelfth": "12th",
}


def ordinal_street(street: str) -> str:
    """'4094 Fourth Ave' -> '4094 4th Avenue' (OpenStreetMap's spelling)."""
    out = re.sub(r"\b(" + "|".join(_ORDINALS) + r")\b", lambda m: _ORDINALS[m.group(1).lower()], street, flags=re.I)
    return re.sub(r"\bAve\b\.?", "Avenue", out)


_zip_cache: dict[str, tuple[float, float] | None] = {}


def zip_center(zip_code: str, client: httpx.Client) -> tuple[float, float] | None:
    """ZIP center from sd_places (seeded ZIP rows), else Nominatim."""
    if zip_code in _zip_cache:
        return _zip_cache[zip_code]
    point = None
    try:
        from app.db import get_conn

        with get_conn() as conn:
            row = conn.execute(
                "SELECT lat, lon FROM sd_places WHERE name = %s OR %s = ANY(aliases) LIMIT 1",
                (zip_code, zip_code),
            ).fetchone()
        if row:
            point = (row["lat"], row["lon"])
    except Exception:
        log.debug("sd_places lookup failed for %s", zip_code)
    if point is None:
        point = _nominatim({"postalcode": zip_code, "country": "United States"}, client)
    _zip_cache[zip_code] = point
    return point


def geocode(location: dict, client: httpx.Client) -> tuple[float, float] | None:
    """Street-level Nominatim result, accepted only if it lands within 8 km of the ZIP's center
    (a "4094 Fourth Ave" lookup once landed on a Fourth Ave in Escondido). Tries the street as
    written and with numbered ordinals, structured and free text. None if nothing passes.
    Cached on disk."""
    a = location.get("address") or {}
    street = (a.get("line") or [""])[0]
    city, zip_code = a.get("city", ""), (a.get("postalCode") or "")[:5]
    center = zip_center(zip_code, client) if zip_code else None
    if street:
        variants = [street] + ([ordinal_street(street)] if ordinal_street(street) != street else [])
        for st in variants:
            for params in (
                {"street": st, "city": city, "postalcode": zip_code, "state": "CA"},
                {"q": f"{st}, {city}, CA {zip_code}"},
            ):
                point = _nominatim(params, client)
                if point and (center is None or _km(point, center) <= MAX_KM_FROM_ZIP):
                    return point
    return None  # never a ZIP centroid: the distance we text people must be real


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def _row(kind: str, location_ref: str, location: dict, name: str, hours: dict, phone: str | None,
         coords: tuple[float, float], walk_in: bool | None) -> dict:
    return {
        "id": row_id(kind, location_ref),
        "name": name,
        "kind": kind,
        "lat": coords[0],
        "lon": coords[1],
        "address": address_text(location),
        "phone": phone,
        "hours": hours,
        "has_xray": None,
        "walk_in": walk_in,
        "accepts": [PLAN_ID],
        "source_url": f"{BASE}/{location_ref}",
    }


def _title(text: str) -> str:
    return text.title() if text.isupper() else text


def fetch_chg_locations(client: httpx.Client | None = None) -> list[dict]:
    """Medi-Cal network urgent cares, labs, and community health center sites in San Diego County,
    as dicts shaped like `locations` rows (lat/lon instead of geom)."""
    own = client is None
    client = client or httpx.Client(timeout=40.0)
    rows: dict[str, dict] = {}
    try:
        # Urgent cares and labs from HealthcareService.
        for category, kind, walk_in in (("urg", "urgent_care", True), ("lab", "lab", None)):
            services = fhir_search(f"HealthcareService?service-category={category}&_count=100", client)
            for svc in services:
                if not is_medi_cal(svc["id"]) or not svc.get("location"):
                    continue
                ref = svc["location"][0]["reference"]
                rid = row_id(kind, ref)
                if rid in rows:
                    continue
                loc = fhir_get(ref, client)
                if not in_san_diego_county((loc.get("address") or {}).get("postalCode")):
                    continue
                coords = geocode(loc, client)
                if not coords:
                    log.warning("no coordinates for %s", ref)
                    continue
                org_name = loc.get("name") or svc.get("name") or kind
                label = "Urgent Care" if kind == "urgent_care" else "Lab"
                name = _title(org_name) if label.lower() in org_name.lower() else f"{_title(org_name)} {label}"
                hours = clean_all_day(hours_from_available_time(svc.get("telecom", [])), allow_all_day=(kind == "urgent_care"))
                rows[rid] = _row(kind, ref, loc, name, hours,
                                 phone_from(svc.get("telecom", [])) or phone_from(loc.get("telecom", [])),
                                 coords, walk_in)

        # Community health centers: provider groups looked up by name, Medi-Cal sites only.
        for org_name in FQHC_ORGS:
            orgs = fhir_search(f"Organization?name={org_name}&_count=20", client, max_pages=1)
            for org in orgs:
                affs = fhir_search(
                    f"OrganizationAffiliation?participating-organization=Organization/{org['id']}"
                    f"&network=Organization/MCALNTWK&_count=100",
                    client,
                    max_pages=5,
                )
                hours_by_loc: dict[str, dict] = {}
                for aff in affs:
                    for loc_ref in (l["reference"] for l in aff.get("location", [])):
                        h = hours_from_available_time(aff.get("telecom", []))
                        if h and loc_ref not in hours_by_loc:
                            hours_by_loc[loc_ref] = h
                        hours_by_loc.setdefault(loc_ref, {})
                for ref, hours in hours_by_loc.items():
                    rid = row_id("fqhc", ref)
                    if rid in rows:
                        continue
                    loc = fhir_get(ref, client)
                    if not in_san_diego_county((loc.get("address") or {}).get("postalCode")):
                        continue
                    hours = clean_all_day(hours or hours_from_location(loc), allow_all_day=False)
                    if not hours:
                        continue  # no hours means we can never say "open now"
                    coords = geocode(loc, client)
                    if not coords:
                        continue
                    site = loc.get("name") or ""
                    name = _title(org["name"])
                    if site and site.lower() not in name.lower() and not re.search(r",\s*(md|do|np|pa)\b", site, re.I):
                        name = f"{name}, {_title(site)}"
                    rows[rid] = _row("fqhc", ref, loc, name, hours, phone_from(loc.get("telecom", [])),
                                     coords, None)
    finally:
        if own:
            client.close()
    # One row per kind and building: departments and floors at one address are listed separately.
    unique: dict[tuple[str, str], dict] = {}
    for r in rows.values():
        unique.setdefault(building_key(r["kind"], r["address"]), r)
    return list(unique.values())


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def load(rows: list[dict], conn) -> dict:
    """Upsert CHG rows. If an existing non-CHG location is within 75 m, tag it with chg_medi_cal
    instead of inserting a duplicate. Returns counts."""
    now = datetime.now(timezone.utc)
    counts = {"inserted_or_updated": 0, "tagged_existing": 0}
    for r in rows:
        match = conn.execute(
            """SELECT id FROM locations
                WHERE id NOT LIKE 'chg\\_%%'
                  AND ST_DWithin(geom, ST_MakePoint(%(lon)s, %(lat)s)::geography, %(m)s)
                ORDER BY ST_Distance(geom, ST_MakePoint(%(lon)s, %(lat)s)::geography)
                LIMIT 1""",
            {"lon": r["lon"], "lat": r["lat"], "m": MATCH_METERS},
        ).fetchone()
        if match:
            conn.execute(
                """UPDATE locations
                      SET accepts = CASE WHEN accepts ? %s THEN accepts ELSE accepts || to_jsonb(%s::text) END
                    WHERE id = %s""",
                (PLAN_ID, PLAN_ID, match["id"]),
            )
            counts["tagged_existing"] += 1
            continue
        conn.execute(
            """INSERT INTO locations (id, name, kind, geom, address, phone, hours, has_xray, walk_in,
                                      accepts, source_url, crawled_at)
               VALUES (%(id)s, %(name)s, %(kind)s, ST_MakePoint(%(lon)s, %(lat)s)::geography, %(address)s,
                       %(phone)s, %(hours)s, %(has_xray)s, %(walk_in)s, %(accepts)s, %(source_url)s, %(now)s)
               ON CONFLICT (id) DO UPDATE SET
                 name = EXCLUDED.name, kind = EXCLUDED.kind, geom = EXCLUDED.geom, address = EXCLUDED.address,
                 phone = EXCLUDED.phone, hours = EXCLUDED.hours, walk_in = EXCLUDED.walk_in,
                 accepts = EXCLUDED.accepts, source_url = EXCLUDED.source_url, crawled_at = EXCLUDED.crawled_at""",
            {**r, "hours": Jsonb(r["hours"]), "accepts": Jsonb(r["accepts"]), "now": now},
        )
        counts["inserted_or_updated"] += 1
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="write to the database (default: print only)")
    parser.add_argument("--url", help="database URL (default: Neon main from .env)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    rows = fetch_chg_locations()
    by_kind: dict[str, int] = {}
    for r in rows:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
    print(f"fetched {len(rows)} San Diego Medi-Cal sites: {by_kind}")
    if not args.apply:
        for r in rows:
            print(f"  {r['id']:28} {r['name'][:50]:50} {r['address'][:50]}")
        return

    from app.db import get_conn

    with get_conn(args.url) as conn:
        print(load(rows, conn))


if __name__ == "__main__":
    main()
