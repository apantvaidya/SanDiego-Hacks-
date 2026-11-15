import httpx

from crawler import crawl, extract
from crawler.crawl import html_to_text, load_targets
from crawler.extract import accepts_for, in_san_diego_county, normalize_hours, normalize_sliding_scale, to_row


def test_targets_file_is_valid():
    targets = load_targets()
    assert len(targets) >= 10
    assert all(t["kind"] in ("urgent_care", "fqhc", "kaiser", "er") and t["url"].startswith("https://") for t in targets)
    assert len({t["key"] for t in targets}) == len(targets)


def test_html_to_text_drops_scripts_and_keeps_phone():
    html = """<html><head><title>x</title><script>var a=1</script></head><body>
      <h1>Clinic</h1><p>Open 8am&ndash;8pm</p><style>.a{}</style>
      <a href="tel:6195551234">Call us</a><ul><li>X-ray on site</li></ul></body></html>"""
    text = html_to_text(html)
    assert "var a" not in text and ".a{}" not in text
    assert "Clinic" in text and "X-ray on site" in text and "6195551234" in text


def test_normalize_hours_ampm_overnight_midnight():
    hours = normalize_hours([
        {"day": "mon", "open": "8:00 AM", "close": "8:00 PM"},
        {"day": "monday", "open": "8:00 AM", "close": "8:00 PM"},  # duplicate
        {"day": "fri", "open": "22:00", "close": "02:00"},
        {"day": "sat", "open": "9am", "close": "12am"},
        {"day": "sun", "open": "closed", "close": "closed"},
    ])
    assert hours == {
        "mon": [["08:00", "20:00"]],
        "fri": [["22:00", "24:00"]],
        "sat": [["00:00", "02:00"], ["09:00", "24:00"]],
    }


def test_normalize_sliding_scale_maps_tiers():
    scale = normalize_sliding_scale([
        {"fpl_max_percent": 100, "fee_low": 0, "fee_high": 25},
        {"fpl_max_percent": 138, "fee_low": 25, "fee_high": 30},
        {"fpl_max_percent": 200, "fee_low": 40, "fee_high": 50},
        {"fpl_max_percent": None, "fee_low": 120, "fee_high": 120},
    ])
    assert scale == {"<138": [0.0, 30.0], "138-200": [40.0, 50.0], ">400": [120.0, 120.0]}
    assert normalize_sliding_scale([]) is None


def test_county_and_accepts():
    assert in_san_diego_county("92103") and in_san_diego_county("91910")
    assert not in_san_diego_county("92260") and not in_san_diego_county(None)
    target = {"accepts": ["kaiser"]}
    assert accepts_for(target, {"self_pay_visit_price": 150}) == ["kaiser", "uninsured"]
    assert "chg_medi_cal" not in accepts_for({"accepts": []}, {"accepts_medi_cal": True})


def test_to_row_ids_and_price_sanity():
    target = {"key": "marque_urgent_care", "kind": "urgent_care", "url": "https://x", "accepts": ["uninsured"]}
    loc = {"name": "Marque UTC", "street": "4085 Governor Dr", "city": "San Diego", "zip": "92122",
           "self_pay_visit_price": 99999, "hours": []}
    single = to_row(target, loc, 1, 1, (32.85, -117.2))
    multi = to_row(target, loc, 2, 3, (32.85, -117.2))
    assert single["id"] == "marque_urgent_care"
    assert multi["id"] == "marque_urgent_care_4085_governor_dr"
    assert single["self_pay_price"] is None  # absurd price dropped
    assert single["walk_in"] is True and single["address"] == "4085 Governor Dr, San Diego, CA 92122"


def test_extract_rows_drops_unplaceable_and_out_of_county(monkeypatch):
    fake = {"locations": [
        {"name": "A", "street": "1 A St", "city": "San Diego", "zip": "92101", "self_pay_visit_price": 150},
        {"name": "B", "street": None, "city": "San Diego", "zip": "92101"},
        {"name": "C", "street": "1 C St", "city": "Palm Springs", "zip": "92262"},
        {"name": "D", "street": "1 D St", "city": "San Diego", "zip": "92102"},
    ]}
    monkeypatch.setattr(extract, "_cached_extract", lambda target, text: fake)
    import crawler.chg_fhir as chg

    monkeypatch.setattr(chg, "geocode", lambda loc, client: None if loc["address"]["line"][0] == "1 D St" else (32.7, -117.1))
    target = {"key": "t", "kind": "urgent_care", "url": "https://x", "accepts": []}
    rows = extract.extract_rows(target, "text", httpx.Client())
    assert [r["name"] for r in rows] == ["A"]
    assert rows[0]["accepts"] == ["uninsured"] and rows[0]["self_pay_price"] == 150.0


def test_fetch_uses_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(crawl, "RAW", tmp_path)
    calls = []
    monkeypatch.setattr(crawl, "fetch_brightdata", lambda url, client: calls.append(url) or "<p>hi</p>")
    assert crawl.fetch("https://x", None) == ("<p>hi</p>", "brightdata")
    assert crawl.fetch("https://x", None) == ("<p>hi</p>", "cache")
    assert calls == ["https://x"]


def test_brightdata_requires_zone(monkeypatch):
    monkeypatch.setattr(crawl.settings, "brightdata_unlocker_zone", "")
    try:
        crawl.fetch_brightdata("https://x", httpx.Client())
        assert False
    except crawl.FetchError as e:
        assert "BRIGHTDATA_UNLOCKER_ZONE" in str(e)


class _Rollback(Exception):
    pass


def test_load_and_patches_against_neon_rolled_back():
    import pytest

    from app.config import settings
    from app.db import get_conn
    from crawler.crawl import apply_patches, load

    if not settings.database_url:
        pytest.skip("DATABASE_URL not set")
    row = {
        "id": "test_crawl_row", "name": "Test Crawl Urgent Care", "kind": "urgent_care",
        "lat": 32.0, "lon": -117.5,  # offshore, far from real sites
        "address": "1 Test St, San Diego, CA 92101", "phone": None,
        "hours": {"mon": [["08:00", "20:00"]]}, "has_xray": True, "walk_in": True,
        "accepts": ["uninsured"], "self_pay_price": 150.0, "sliding_scale": None, "source_url": "https://x",
    }
    patch = {"match_name": "Test Crawl Urgent", "kind": "urgent_care", "hours": None, "walk_in": None,
             "has_xray": None, "self_pay_price": 175.0, "sliding_scale": None, "accepts": ["kaiser"],
             "source_url": "https://y"}
    try:
        with get_conn() as conn:
            assert load([row], conn) == {"updated_existing": 0, "inserted": 1}
            assert load([{**row, "id": "other_id", "self_pay_price": 160.0}], conn)["updated_existing"] == 1
            assert apply_patches([patch], conn)["patched_rows"] == 1
            got = conn.execute("SELECT self_pay_price, accepts FROM locations WHERE id = 'test_crawl_row'").fetchone()
            assert float(got["self_pay_price"]) == 175.0
            assert got["accepts"] == ["kaiser", "uninsured"]
            assert conn.execute("SELECT count(*) FROM locations WHERE id = 'other_id'").fetchone()["count"] == 0
            raise _Rollback
    except _Rollback:
        pass
    with get_conn() as conn:
        assert conn.execute("SELECT count(*) FROM locations WHERE id LIKE 'test_crawl%%'").fetchone()["count"] == 0
