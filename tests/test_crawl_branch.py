"""#13: Neon branch API (faked), validation rules (pure), swap/rollback (test database only)."""
from datetime import datetime, timezone

import httpx
import pytest

from app.config import settings
from crawler import branch
from crawler.validate import hours_ok, metrics, problems


def test_crawl_branch_name():
    assert branch.crawl_branch_name(datetime(2026, 11, 15, 9, 5, tzinfo=timezone.utc)) == "crawl-2026-11-15-0905"


def test_swap_host_keeps_credentials_and_params():
    url = "postgresql://owner:pw@ep-green-1.c-4.us-west-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
    assert branch.swap_host(url, "ep-new-2.c-4.us-west-2.aws.neon.tech") == (
        "postgresql://owner:pw@ep-new-2.c-4.us-west-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
    )


def test_connection_string_prefers_returned_uri():
    created = {"connection_uris": [{"connection_uri": "postgresql://x@h/db"}], "endpoints": [{"host": "other"}]}
    assert branch.connection_string(created, "postgresql://a:b@main/db") == "postgresql://x@h/db"
    assert branch.connection_string({"endpoints": [{"host": "new"}]}, "postgresql://a:b@main/db") == "postgresql://a:b@new/db"
    with pytest.raises(branch.NeonError):
        branch.connection_string({"endpoints": []}, "postgresql://a:b@main/db")


def _fake_neon(monkeypatch, handler):
    monkeypatch.setattr(settings, "neon_api_key", "k")
    monkeypatch.setattr(settings, "neon_project_id", "proj")
    monkeypatch.setattr(settings, "database_url", "postgresql://a:b@main.neon.tech/neondb?sslmode=require")
    monkeypatch.setattr(branch.time, "sleep", lambda s: None)
    monkeypatch.setattr(branch, "_client", lambda: httpx.Client(base_url=branch.API, transport=httpx.MockTransport(handler)))


def test_create_branch_waits_for_operations_and_retries_lock(monkeypatch):
    calls = []
    op_polls = {"n": 0}

    def handler(request: httpx.Request):
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            if sum(1 for c in calls if c[0] == "POST") == 1:
                return httpx.Response(423, json={"message": "locked"})
            body = request.read().decode()
            assert '"read_write"' in body and '"crawl-x"' in body
            return httpx.Response(201, json={
                "branch": {"id": "br-123", "name": "crawl-x"},
                "endpoints": [{"host": "ep-new.neon.tech"}],
                "operations": [{"id": "op1", "status": "running"}],
            })
        if "/operations/op1" in request.url.path:
            op_polls["n"] += 1
            return httpx.Response(200, json={"operation": {"status": "finished" if op_polls["n"] > 1 else "running"}})
        return httpx.Response(404)

    _fake_neon(monkeypatch, handler)
    branch_id, url = branch.create_branch("crawl-x")
    assert branch_id == "br-123"
    assert url == "postgresql://a:b@ep-new.neon.tech/neondb?sslmode=require"
    assert op_polls["n"] == 2
    assert calls[0] == ("POST", "/api/v2/projects/proj/branches")


def test_create_branch_failed_operation_raises(monkeypatch):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(201, json={"branch": {"id": "b"}, "endpoints": [{"host": "h"}],
                                             "operations": [{"id": "op", "status": "running"}]})
        return httpx.Response(200, json={"operation": {"status": "failed"}})

    _fake_neon(monkeypatch, handler)
    with pytest.raises(branch.NeonError, match="failed"):
        branch.create_branch("crawl-y")


def test_delete_branch_ignores_404(monkeypatch):
    _fake_neon(monkeypatch, lambda r: httpx.Response(404))
    branch.delete_branch("gone")


def test_missing_api_key(monkeypatch):
    monkeypatch.setattr(settings, "neon_api_key", "")
    with pytest.raises(branch.NeonError, match="NEON_API_KEY"):
        branch._client()


# --- validation rules -------------------------------------------------------------------------

def test_hours_ok():
    assert hours_ok({"mon": [["08:00", "20:00"]], "sat": [["00:00", "24:00"]]})
    assert hours_ok({})
    assert not hours_ok({"monday": [["08:00", "20:00"]]})
    assert not hours_ok({"mon": [["8am", "8pm"]]})
    assert not hours_ok({"mon": [["20:00", "08:00"]]})
    assert not hours_ok(None)


def _row(i, accepts=("uninsured", "kaiser", "chg_medi_cal"), lat=32.8, hours=None, price=150):
    return {"id": f"r{i}", "kind": "urgent_care", "hours": hours if hours is not None else {"mon": [["08:00", "20:00"]]},
            "accepts": list(accepts), "source_url": "https://x", "self_pay_price": price, "lat": lat, "lon": -117.1}


def test_good_crawl_passes():
    m = metrics([_row(i) for i in range(10)], main_count=10)
    assert problems(m) == []


def test_drop_outside_bad_hours_coverage_fail():
    rows = [_row(i) for i in range(5)] + [_row(98, lat=34.05), _row(99, hours={"mon": [["9", "5"]]})]
    found = problems(metrics(rows, main_count=20))
    assert any("dropped from 20 to 7" in p for p in found)
    assert any("outside San Diego County: r98" in p for p in found)
    assert any("unparseable hours: r99" in p for p in found)
    found = problems(metrics([_row(1, accepts=("uninsured",)), _row(2)], main_count=2))
    assert "only 1 rows accept kaiser" in found


def test_empty_crawl_fails_and_soft_issues_dont():
    assert problems(metrics([], main_count=50)) == ["no rows in the crawl branch",
                                                     "only 0 rows accept kaiser", "only 0 rows accept chg_medi_cal",
                                                     "only 0 rows accept uninsured"]
    m = metrics([_row(i, hours={}, price=None) for i in range(4)], main_count=4)
    assert problems(m) == [] and len(m["no_hours"]) == 4 and len(m["uninsured_urgent_no_price"]) == 4


# --- swap / rollback against the test database only ---------------------------------------------

def test_swap_and_rollback_on_test_database():
    if not settings.database_url:
        pytest.skip("TEST_DATABASE_URL not set")
    from app.db import get_conn
    from crawler.swap import read_rows, rollback, swap_into_main

    with get_conn() as conn:
        before = read_rows(conn)
        assert before, "seeds should be loaded"
        n = swap_into_main(conn, conn)  # same rows in: count and content unchanged
        assert n == len(before)
        after = read_rows(conn)
        assert [r["id"] for r in after] == [r["id"] for r in before]
        assert [r["geom_ewkt"] for r in after] == [r["geom_ewkt"] for r in before]
        prev = conn.execute("SELECT count(*) AS n FROM locations_prev").fetchone()["n"]
        assert prev == len(before)
        conn.execute("DELETE FROM locations WHERE id = %s", (before[0]["id"],))
        assert rollback(conn) == len(before)
        assert len(read_rows(conn)) == len(before)
