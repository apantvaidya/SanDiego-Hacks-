"""Test setup: tests never touch the production database.

Database tests run against TEST_DATABASE_URL, a separate Neon branch (e.g. `tests`), never
against DATABASE_URL (production, which real users and crawls write to). Before the first
test, the test branch is reset: schema applied, locations and plans emptied and reseeded
from db/seed, sd_places reseeded. So results never depend on what someone loaded into
production, and tests that create users never write to it.

  * TEST_DATABASE_URL set: database tests use it (settings.database_url is pointed at it).
  * Not set: settings.database_url is blanked, so every database test skips.
  * Set to the same Neon endpoint as DATABASE_URL: the run stops with an error.

Live Gemini / Nominatim tests stay behind RUN_LIVE_GEMINI=1 / RUN_LIVE_GEO=1.
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

import pytest
from dotenv import dotenv_values

from app.config import settings

ROOT = Path(__file__).resolve().parent.parent
_env = dotenv_values(ROOT / ".env") if (ROOT / ".env").exists() else {}
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or _env.get("TEST_DATABASE_URL") or ""
PRODUCTION_URL = settings.database_url


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").replace("-pooler", "")


if TEST_DATABASE_URL and PRODUCTION_URL and _host(TEST_DATABASE_URL) == _host(PRODUCTION_URL):
    pytest.exit("TEST_DATABASE_URL points at the production Neon endpoint. Use a separate Neon branch.",
                returncode=2)

# Every test module reads settings.database_url at import, after this file runs.
settings.database_url = TEST_DATABASE_URL


def _reset_test_database() -> None:
    from app.db import apply_sql_files, get_conn

    apply_sql_files([str(ROOT / "db/schema.sql")])
    with get_conn() as conn:
        conn.execute("TRUNCATE locations, locations_prev, sd_places")
        conn.execute("UPDATE profiles SET plan_id = NULL WHERE plan_id IS NOT NULL")
        conn.execute("DELETE FROM plans")
    apply_sql_files([str(ROOT / "db/seed/seed_locations.sql"), str(ROOT / "db/seed/seed_sd_places.sql")])


@pytest.fixture(scope="session", autouse=True)
def test_database():
    """Reset the test branch once per run (only when TEST_DATABASE_URL is set)."""
    if TEST_DATABASE_URL:
        _reset_test_database()
    yield TEST_DATABASE_URL


def pytest_report_header(config):
    if TEST_DATABASE_URL:
        return f"database tests: TEST_DATABASE_URL ({_host(TEST_DATABASE_URL)}), reset to seeds"
    return "database tests: skipped (set TEST_DATABASE_URL to a Neon test branch to run them)"
