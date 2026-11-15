"""Bright Data crawl of clinic and urgent care pages, Gemini extraction, load into a database.

    uv run python -m crawler.crawl                     # fetch + extract, print rows, write nothing
    uv run python -m crawler.crawl --apply --url URL   # also load rows into that database (a crawl branch)
    uv run python -m crawler.crawl --direct            # development only: fetch without Bright Data

Fetching: Bright Data Web Unlocker through the Direct API
(POST https://api.brightdata.com/request, Bearer BRIGHTDATA_API_TOKEN, zone BRIGHTDATA_UNLOCKER_ZONE).
Several targets block plain requests (AFC 500, Kaiser 302 loop), which is the
reason we use Web Unlocker. `--direct` exists only to develop extraction before a zone exists.

Caching: raw pages in crawler/raw/pages/, extractions in crawler/raw/extract/ (both gitignored),
so a re-run costs no Bright Data credits and no Gemini calls. Delete them to refresh.

Loading (`load`): a crawled site within 75 m of an existing row updates that row (prices, hours,
X-ray, walk-in, sliding scale when the page has them; accepts are merged), otherwise it is
inserted with the target's stable key. Per CLAUDE.md, crawls go to a Neon branch first (#13),
not straight to main.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

import httpx
import yaml
from psycopg.types.json import Jsonb

from app.config import settings

log = logging.getLogger(__name__)

BRIGHTDATA_REQUEST = "https://api.brightdata.com/request"
RAW = Path("crawler/raw/pages")
MAX_TEXT = 40_000
MATCH_METERS = 75
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------

def load_targets(path: str = "crawler/targets.yaml") -> list[dict]:
    data = yaml.safe_load(Path(path).read_text()) or {}
    targets = []
    for kind, items in data.items():
        for item in items or []:
            targets.append({**item, "kind": kind, "accepts": list(item.get("accepts") or [])})
    keys = [t["key"] for t in targets]
    dupes = {k for k in keys if keys.count(k) > 1}
    if dupes:
        raise ValueError(f"duplicate target keys: {sorted(dupes)}")
    return targets


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

class FetchError(RuntimeError):
    pass


def _raw_path(url: str) -> Path:
    return RAW / (hashlib.sha1(url.encode()).hexdigest() + ".html")


def fetch_brightdata(url: str, client: httpx.Client) -> str:
    zone = settings.brightdata_unlocker_zone
    if not settings.brightdata_api_token or not zone:
        raise FetchError("set BRIGHTDATA_API_TOKEN and BRIGHTDATA_UNLOCKER_ZONE (Web Unlocker zone name)")
    resp = client.post(
        BRIGHTDATA_REQUEST,
        headers={"Authorization": f"Bearer {settings.brightdata_api_token}"},
        json={"zone": zone, "url": url, "format": "raw"},
        timeout=90.0,
    )
    if resp.status_code >= 400:
        raise FetchError(f"Bright Data {resp.status_code}: {resp.text[:200]}")
    return resp.text


def fetch_direct(url: str, client: httpx.Client) -> str:
    resp = client.get(url, headers={"User-Agent": BROWSER_UA}, follow_redirects=True, timeout=30.0)
    if resp.status_code >= 400:
        raise FetchError(f"direct {resp.status_code}")
    return resp.text


def fetch(url: str, client: httpx.Client, direct: bool = False) -> tuple[str, str]:
    """Return (html, how) where how is 'cache', 'brightdata', or 'direct'."""
    path = _raw_path(url)
    if path.exists():
        return path.read_text(), "cache"
    html = fetch_direct(url, client) if direct else fetch_brightdata(url, client)
    RAW.mkdir(parents=True, exist_ok=True)
    path.write_text(html)
    return html, "direct" if direct else "brightdata"


# ---------------------------------------------------------------------------
# HTML to text
# ---------------------------------------------------------------------------

class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head", "template"}
    BLOCK = {"p", "div", "li", "tr", "br", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article",
             "table", "ul", "ol", "dt", "dd", "address", "footer", "header"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "a":
            href = dict(attrs).get("href") or ""
            if href.startswith("tel:"):
                self.parts.append(f" {href[4:]} ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


_LD_JSON = re.compile(r"(?is)<script[^>]*application/ld\+json[^>]*>(.*?)</script>")


def _ld_hours(html: str) -> str:
    """Opening hours from schema.org structured data (AFC puts them only there), as text lines
    the extractor can read. Empty when the page has none."""
    lines = []
    for block in _LD_JSON.findall(html):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        stack = [data]
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                for spec in item.get("openingHoursSpecification") or []:
                    if not isinstance(spec, dict):
                        continue
                    days = spec.get("dayOfWeek")
                    for day in days if isinstance(days, list) else [days]:
                        if day and spec.get("opens") and spec.get("closes"):
                            lines.append(f"{str(day).rsplit('/', 1)[-1]} {spec['opens'][:5]}-{spec['closes'][:5]}")
                stack.extend(v for v in item.values() if isinstance(v, (dict, list)))
    unique = list(dict.fromkeys(lines))
    return ("Opening hours (page structured data): " + "; ".join(unique) + "\n") if unique else ""


def html_to_text(html: str, limit: int = MAX_TEXT) -> str:
    parser = _Text()
    parser.feed(html)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return (_ld_hours(html) + text.strip())[:limit]


# ---------------------------------------------------------------------------
# Crawl
# ---------------------------------------------------------------------------

def run_crawl(targets_path: str = "crawler/targets.yaml", direct: bool = False,
              only: list[str] | None = None) -> tuple[list[dict], list[dict], list[dict]]:
    """Fetch every target and extract. Returns (rows, patches, report); report has one entry per
    target: key, url, how it was fetched, row and patch counts, error."""
    from crawler.extract import extract_patches, extract_rows

    rows: list[dict] = []
    patches: list[dict] = []
    report: list[dict] = []
    with httpx.Client() as client:
        for target in load_targets(targets_path):
            if only and target["key"] not in only:
                continue
            entry = {"key": target["key"], "url": target["url"], "how": None, "rows": 0, "patches": 0, "error": None}
            try:
                html, how = fetch(target["url"], client, direct=direct)
                entry["how"] = how
                text = html_to_text(html)
                if len(text) < 200:
                    raise FetchError(f"page text too short ({len(text)} chars), probably blocked")
                found = extract_rows(target, text, client)
                if target.get("single") and found:
                    # Single-site page: nearby sites it mentions are not this target.
                    found = [{**found[0], "id": target["key"]}]
                rows.extend(found)
                entry["rows"] = len(found)
                fixes = extract_patches(target, text)
                patches.extend(fixes)
                entry["patches"] = len(fixes)
            except Exception as exc:  # one bad page must not stop the crawl
                entry["error"] = str(exc)[:200]
                log.warning("%s: %s", target["key"], entry["error"])
            report.append(entry)
            if entry["how"] in ("brightdata", "direct"):
                time.sleep(1.0)
    return rows, patches, report


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

_UPDATABLE = ("hours", "has_xray", "walk_in", "self_pay_price", "sliding_scale", "phone")


def load(rows: list[dict], conn) -> dict:
    """Upsert crawled rows. Updates the nearest existing row within 75 m (only fields the page
    actually had, accepts merged) or inserts a new row with the stable key."""
    now = datetime.now(timezone.utc)
    counts = {"updated_existing": 0, "inserted": 0}
    for r in rows:
        match = conn.execute(
            """SELECT id, accepts FROM locations
                WHERE ST_DWithin(geom, ST_MakePoint(%(lon)s, %(lat)s)::geography, %(m)s)
                  AND kind = %(kind)s
                ORDER BY ST_Distance(geom, ST_MakePoint(%(lon)s, %(lat)s)::geography)
                LIMIT 1""",
            {"lon": r["lon"], "lat": r["lat"], "m": MATCH_METERS, "kind": r["kind"]},
        ).fetchone()
        if match:
            sets, params = [], {"id": match["id"], "now": now}
            for col in _UPDATABLE:
                if r.get(col) not in (None, {}, []):
                    sets.append(f"{col} = %({col})s")
                    params[col] = Jsonb(r[col]) if col in ("hours", "sliding_scale") else r[col]
            merged = sorted(set(match["accepts"] or []) | set(r["accepts"]))
            sets.append("accepts = %(accepts)s")
            params["accepts"] = Jsonb(merged)
            sets.append("crawled_at = %(now)s")
            conn.execute(f"UPDATE locations SET {', '.join(sets)} WHERE id = %(id)s", params)
            counts["updated_existing"] += 1
            continue
        conn.execute(
            """INSERT INTO locations (id, name, kind, geom, address, phone, hours, has_xray, walk_in,
                                      accepts, self_pay_price, sliding_scale, source_url, crawled_at)
               VALUES (%(id)s, %(name)s, %(kind)s, ST_MakePoint(%(lon)s, %(lat)s)::geography, %(address)s,
                       %(phone)s, %(hours)s, %(has_xray)s, %(walk_in)s, %(accepts)s, %(self_pay_price)s,
                       %(sliding_scale)s, %(source_url)s, %(now)s)
               ON CONFLICT (id) DO UPDATE SET
                 name = EXCLUDED.name, geom = EXCLUDED.geom, address = EXCLUDED.address,
                 phone = COALESCE(EXCLUDED.phone, locations.phone), hours = EXCLUDED.hours,
                 has_xray = COALESCE(EXCLUDED.has_xray, locations.has_xray),
                 walk_in = COALESCE(EXCLUDED.walk_in, locations.walk_in), accepts = EXCLUDED.accepts,
                 self_pay_price = COALESCE(EXCLUDED.self_pay_price, locations.self_pay_price),
                 sliding_scale = COALESCE(EXCLUDED.sliding_scale, locations.sliding_scale),
                 source_url = EXCLUDED.source_url, crawled_at = EXCLUDED.crawled_at""",
            {
                **r,
                "hours": Jsonb(r["hours"]),
                "accepts": Jsonb(r["accepts"]),
                "sliding_scale": Jsonb(r["sliding_scale"]) if r.get("sliding_scale") else None,
                "now": now,
            },
        )
        counts["inserted"] += 1
    return counts


def apply_patches(patches: list[dict], conn) -> dict:
    """Update existing rows whose name contains match_name (same kind, or any clinic kind for
    org-level patches). Only fields the page had are set; accepts are merged."""
    counts = {"patched_rows": 0, "unmatched": []}
    for p in patches:
        sets, params = [], {"name": f"%{p['match_name']}%", "kind": p["kind"], "now": datetime.now(timezone.utc)}
        for col in _UPDATABLE:
            if p.get(col) not in (None, {}, []):
                sets.append(f"{col} = %({col})s")
                params[col] = Jsonb(p[col]) if col in ("hours", "sliding_scale") else p[col]
        sets.append("accepts = (SELECT jsonb_agg(DISTINCT x ORDER BY x) FROM jsonb_array_elements_text(accepts || %(accepts)s) x)")
        params["accepts"] = Jsonb(p["accepts"])
        sets.append("crawled_at = %(now)s")
        res = conn.execute(
            f"UPDATE locations SET {', '.join(sets)} WHERE name ILIKE %(name)s AND kind = %(kind)s RETURNING id",
            params,
        ).fetchall()
        if res:
            counts["patched_rows"] += len(res)
        else:
            counts["unmatched"].append(p["match_name"])
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Bright Data crawl + Gemini extraction")
    parser.add_argument("--direct", action="store_true", help="development only: fetch without Bright Data")
    parser.add_argument("--only", nargs="*", help="target keys to crawl")
    parser.add_argument("--apply", action="store_true", help="load rows into the database")
    parser.add_argument("--url", help="database URL to load into (a Neon crawl branch); default DATABASE_URL")
    parser.add_argument("--out", default="crawler/raw/crawl_rows.json", help="where to save extracted rows")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    rows, patches, report = run_crawl(direct=args.direct, only=args.only)
    for e in report:
        status = f"{e['rows']} rows, {e['patches']} patches" if not e["error"] else f"ERROR {e['error']}"
        print(f"  {e['key']:32} {e['how'] or '-':10} {status}")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({"rows": rows, "patches": patches}, indent=1, default=str))
    print(f"{len(rows)} rows, {len(patches)} patches -> {args.out}")
    for p in patches:
        print(f"  patch '{p['match_name'][:45]}' walk_in={p['walk_in']} hours={'yes' if p['hours'] else 'no'} accepts={p['accepts']}")
    for r in rows:
        print(f"  {r['id']:34} {r['name'][:40]:40} ${r['self_pay_price']} xray={r['has_xray']} {r['address'][:40]}")
    if args.apply:
        from app.db import get_conn

        with get_conn(args.url) as conn:
            print(load(rows, conn))
            print(apply_patches(patches, conn))


if __name__ == "__main__":
    main()
