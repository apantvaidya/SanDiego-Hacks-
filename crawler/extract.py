"""Gemini extraction of crawled page text into `locations` rows.

Gemini reads the page text and returns structured facts (address parts, hours, prices, X-ray,
walk-in, sliding scale, insurance mentions). Code then validates and normalizes: San Diego
County ZIPs only, hours in our {"mon": [["08:00","20:00"]]} format, prices as plain numbers,
geocoded coordinates (same checks as the CHG loader). Gemini never invents a row we keep:
rows without a street address, a San Diego ZIP, and coordinates are dropped.

Extractions are cached in crawler/raw/extract/ by page-text hash, so re-runs make no Gemini calls.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

CACHE = Path("crawler/raw/extract")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
FPL_TIERS = ("<138", "138-200", "200-400", ">400")

SYSTEM = (
    "You extract facts about medical clinics from web page text. Only report what the text "
    "states. Never guess an address, price, or hours. Use null when the page does not say. "
    "Times are 24-hour HH:MM local time. Prices are US dollars as numbers."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "locations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "street": {"type": ["string", "null"], "description": "street line, no suite"},
                    "city": {"type": ["string", "null"]},
                    "zip": {"type": ["string", "null"]},
                    "phone": {"type": ["string", "null"]},
                    "hours": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "day": {"type": "string", "enum": list(DAYS)},
                                "open": {"type": "string"},
                                "close": {"type": "string"},
                            },
                            "required": ["day", "open", "close"],
                        },
                    },
                    "walk_in": {"type": ["boolean", "null"]},
                    "has_xray": {"type": ["boolean", "null"]},
                    "self_pay_visit_price": {"type": ["number", "null"], "description": "basic office visit, no insurance"},
                    "sliding_scale": {
                        "type": "array",
                        "description": "fee per visit by income as percent of federal poverty level",
                        "items": {
                            "type": "object",
                            "properties": {
                                "fpl_max_percent": {"type": ["number", "null"]},
                                "fee_low": {"type": "number"},
                                "fee_high": {"type": "number"},
                            },
                            "required": ["fee_low", "fee_high"],
                        },
                    },
                    "accepts_uninsured": {"type": ["boolean", "null"]},
                    "accepts_medi_cal": {"type": ["boolean", "null"]},
                    "accepts_kaiser": {"type": ["boolean", "null"]},
                },
                "required": ["name"],
            },
        }
    },
    "required": ["locations"],
}


# ---------------------------------------------------------------------------
# Normalization (pure, unit tested)
# ---------------------------------------------------------------------------

def _time(value: str | None) -> str | None:
    if not value:
        return None
    m = re.match(r"^\s*(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?\s*$", value.strip().lower())
    if not m:
        return None
    h, mins, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ampm:
        if h == 12:
            h = 0
        if ampm.startswith("p"):
            h += 12
    if h == 24 and mins == 0:
        return "24:00"
    if not (0 <= h < 24 and 0 <= mins < 60):
        return None
    return f"{h:02d}:{mins:02d}"


def normalize_hours(items: list[dict] | None) -> dict:
    hours: dict[str, list[list[str]]] = {}
    for item in items or []:
        day = (item.get("day") or "").lower()[:3]
        start, end = _time(item.get("open")), _time(item.get("close"))
        if day not in DAYS or not start or not end:
            continue
        if end == "00:00":
            end = "24:00"
        if end <= start and end != "24:00":
            nxt = DAYS[(DAYS.index(day) + 1) % 7]
            hours.setdefault(day, []).append([start, "24:00"])
            hours.setdefault(nxt, []).append(["00:00", end])
        else:
            span = [start, end]
            if span not in hours.get(day, []):
                hours.setdefault(day, []).append(span)
    return {d: sorted(hours[d]) for d in DAYS if d in hours}


def normalize_sliding_scale(items: list[dict] | None) -> dict | None:
    """[{fpl_max_percent: 100, fee_low: 0, fee_high: 25}, ...] -> {"<138": [0, 25], ...}.
    Bands are mapped onto our four FPL tiers by their upper bound."""
    if not items:
        return None
    out: dict[str, list[float]] = {}
    for item in items:
        low, high = item.get("fee_low"), item.get("fee_high")
        if low is None or high is None:
            continue
        cap = item.get("fpl_max_percent")
        if cap is None:
            tier = ">400"
        elif cap <= 138:
            tier = "<138"
        elif cap <= 200:
            tier = "138-200"
        elif cap <= 400:
            tier = "200-400"
        else:
            tier = ">400"
        lo, hi = float(min(low, high)), float(max(low, high))
        if tier in out:
            lo, hi = min(lo, out[tier][0]), max(hi, out[tier][1])
        out[tier] = [lo, hi]
    return out or None


def in_san_diego_county(zip_code: str | None) -> bool:
    return bool(zip_code) and bool(re.match(r"^9(19|20|21)\d{2}$", zip_code.strip()[:5]))


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40]


def accepts_for(target: dict, loc: dict) -> list[str]:
    accepts = set(target.get("accepts") or [])
    if loc.get("accepts_uninsured") or loc.get("self_pay_visit_price") is not None or loc.get("sliding_scale"):
        accepts.add("uninsured")
    if loc.get("accepts_kaiser"):
        accepts.add("kaiser")
    # A page saying "we accept Medi-Cal" does not tell us which Medi-Cal plan; CHG membership
    # comes from the CHG directory (#11), so we don't add chg_medi_cal from page text.
    return sorted(accepts)


def to_row(target: dict, loc: dict, index: int, total: int, coords: tuple[float, float]) -> dict:
    name = (loc.get("name") or "").strip() or target["key"].replace("_", " ").title()
    street = (loc.get("street") or "").strip()
    # Stable across re-crawls: single-site pages use the target key, multi-site pages add the street.
    rid = target["key"] if total == 1 else f"{target['key']}_{slug(street)}"
    city = (loc.get("city") or "").strip()
    zip_code = (loc.get("zip") or "").strip()[:5]
    price = loc.get("self_pay_visit_price")
    return {
        "id": rid,
        "name": name,
        "kind": target["kind"],
        "lat": coords[0],
        "lon": coords[1],
        "address": f"{street}, {city}, CA {zip_code}",
        "phone": loc.get("phone"),
        "hours": normalize_hours(loc.get("hours")),
        "has_xray": loc.get("has_xray"),
        "walk_in": loc.get("walk_in") if loc.get("walk_in") is not None else (True if target["kind"] == "urgent_care" else None),
        "accepts": accepts_for(target, loc),
        "self_pay_price": float(price) if isinstance(price, (int, float)) and 0 < price < 2000 else None,
        "sliding_scale": normalize_sliding_scale(loc.get("sliding_scale")),
        "source_url": target["url"],
    }


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def _cached_extract(target: dict, text: str) -> dict:
    from app.llm.gemini import generate_json

    key = hashlib.sha1((target["key"] + "\n" + text).encode()).hexdigest()
    path = CACHE / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text())
    prompt = (
        f"Page: {target['url']}\nSite type: {target['kind']}\nLook for: {target.get('hint', '')}\n"
        "List each physical San Diego County location on this page with the facts the text states.\n\n"
        f"PAGE TEXT:\n{text}"
    )
    data = generate_json(prompt, SCHEMA, system=SYSTEM)
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return data


def extract_location(raw: dict) -> dict:
    """Contract from docs/interfaces.md: raw = {"target": {...}, "text": "..."} -> Gemini output."""
    return _cached_extract(raw["target"], raw["text"])


def to_patch(target: dict, loc: dict | None, match_name: str) -> dict:
    """Facts that update existing rows whose name contains `match_name` (no new row)."""
    loc = loc or {}
    return {
        "match_name": match_name,
        "kind": target["kind"],
        "hours": normalize_hours(loc.get("hours")) or None,
        "walk_in": loc.get("walk_in"),
        "has_xray": loc.get("has_xray"),
        "self_pay_price": (float(loc["self_pay_visit_price"])
                           if isinstance(loc.get("self_pay_visit_price"), (int, float)) and 0 < loc["self_pay_visit_price"] < 2000
                           else None),
        "sliding_scale": normalize_sliding_scale(loc.get("sliding_scale")),
        "accepts": accepts_for(target, loc),
        "source_url": target["url"],
    }


def extract_patches(target: dict, text: str) -> list[dict]:
    """Org-level targets (apply_to_name) patch every matching row; named locations without a
    street patch rows with that name."""
    data = _cached_extract(target, text)
    locs = data.get("locations", [])
    if target.get("apply_to_name"):
        merged: dict = {}
        for loc in locs:
            for k, v in loc.items():
                if v not in (None, [], "") and k not in merged:
                    merged[k] = v
        return [to_patch(target, merged, target["apply_to_name"])]
    return [
        to_patch(target, loc, loc["name"].strip())
        for loc in locs
        if not (loc.get("street") or "").strip() and len((loc.get("name") or "").strip()) >= 8
    ]


def extract_rows(target: dict, text: str, client: httpx.Client) -> list[dict]:
    from crawler.chg_fhir import geocode

    if target.get("apply_to_name"):
        return []
    data = _cached_extract(target, text)
    locs = [
        loc for loc in data.get("locations", [])
        if (loc.get("street") or "").strip() and in_san_diego_county(loc.get("zip"))
    ]
    rows = []
    for i, loc in enumerate(locs, start=1):
        fhir_like = {"address": {"line": [loc["street"]], "city": loc.get("city") or "", "postalCode": loc["zip"][:5]}}
        coords = geocode(fhir_like, client)
        if not coords:
            log.warning("%s: could not place %s", target["key"], loc["street"])
            continue
        rows.append(to_row(target, loc, i, len(locs), coords))
    return rows
