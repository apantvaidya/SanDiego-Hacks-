"""Live re-check of the #1 option, right before we text it. Writes a row to `verifications`.

What "verified X min ago" means (keep the claim this narrow):
  * The source was fetched just now and still lists this place:
      - CHG directory sites (source_url on the CHG FHIR API): the FHIR Location is fetched
        directly (public API, no Bright Data needed) and its status is "active".
      - Clinic websites: the page is fetched through Bright Data Web Unlocker and still shows the
        clinic's name or street number, with no "temporarily / permanently closed" notice.
  * open_now = the stored hours say open now AND the source shows no closure. We don't parse
    today's hours off the page.

If the check can't run (no Bright Data zone yet), verify_live raises NotImplementedError and
the pipeline falls back to the "checked <crawl time>" label, which is the honest one.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import httpx
from psycopg.types.json import Jsonb

from app.config import settings
from app.models import Location, Verification

log = logging.getLogger(__name__)

CHG_FHIR = "https://api-chgsd-prd.safhir.io/v1/api/provider-directory/"
BRIGHTDATA_REQUEST = "https://api.brightdata.com/request"
CLOSED = re.compile(
    r"\b(temporarily closed|permanently closed|closed until further notice|this (location|clinic|office) (is|has) (now )?closed|"
    r"cerrad[oa] temporalmente|cerrad[oa] permanentemente)\b",
    re.I,
)


class VerifyError(RuntimeError):
    pass


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text)


def page_lists(location: Location, text: str) -> bool:
    """The page still mentions this place: its street number + first street word, or a
    distinctive part of its name."""
    t = text.lower()
    m = re.match(r"\s*(\d+)\s+([A-Za-z]+)", location.address or "")
    if m and re.search(rf"\b{m.group(1)}\s+{m.group(2).lower()}", t):
        return True
    words = [w for w in re.findall(r"[a-z]+", (location.name or "").lower())
             if w not in {"urgent", "care", "center", "clinic", "health", "of", "the", "san", "diego", "family"}]
    return bool(words) and all(w in t for w in words[:2])


def check_chg(location: Location, timeout_s: float) -> dict:
    resp = httpx.get(location.source_url, headers={"Accept": "application/fhir+json"}, timeout=timeout_s)
    if resp.status_code == 404:
        return {"how": "chg_fhir", "listed": False, "closed": True, "status": 404}
    if resp.status_code >= 400:
        raise VerifyError(f"CHG directory {resp.status_code}")
    data = resp.json()
    status = data.get("status")
    return {"how": "chg_fhir", "listed": data.get("resourceType") == "Location", "closed": status not in (None, "active"),
            "status": status}


def check_web(location: Location, timeout_s: float) -> dict:
    zone = settings.brightdata_unlocker_zone
    if not settings.brightdata_api_token or not zone:
        raise NotImplementedError("no Bright Data Web Unlocker zone configured")
    resp = httpx.post(
        BRIGHTDATA_REQUEST,
        headers={"Authorization": f"Bearer {settings.brightdata_api_token}"},
        json={"zone": zone, "url": location.source_url, "format": "raw"},
        timeout=timeout_s,
    )
    if resp.status_code >= 400:
        raise VerifyError(f"Bright Data {resp.status_code}")
    text = _strip_html(resp.text)
    closed = CLOSED.search(text)
    return {"how": "brightdata", "listed": page_lists(location, text), "closed": bool(closed),
            "closed_notice": closed.group(0) if closed else None, "chars": len(text)}


def decide(location: Location, check: dict, at: datetime | None = None) -> bool | None:
    """open_now for the Verification. None when the page no longer lists the place (we can't
    vouch for it either way)."""
    from app.care.find_care import is_open_now

    if check.get("closed"):
        return False
    if not check.get("listed"):
        return None
    return is_open_now(location.hours, at)


def _record(v: Verification) -> None:
    try:
        from app.db import get_conn

        with get_conn() as conn:
            conn.execute(
                "INSERT INTO verifications (location_id, checked_at, open_now, raw) VALUES (%s, %s, %s, %s)",
                (v.location_id, v.checked_at, v.open_now, Jsonb(v.raw)),
            )
    except Exception:
        log.exception("could not record verification for %s", v.location_id)


def verify_live(location: Location, timeout_s: float = 6.0) -> Verification:
    if not location.source_url:
        raise NotImplementedError("no source_url to check")
    if location.source_url.startswith(CHG_FHIR):
        check = check_chg(location, timeout_s)
    else:
        check = check_web(location, timeout_s)
    now = datetime.now(timezone.utc)
    v = Verification(location_id=location.id, checked_at=now, open_now=decide(location, check, now), raw=check)
    _record(v)
    return v
