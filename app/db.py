"""Database connection helper. Owned by the Neon setup issue (#2).

Usage:
    from app.db import get_conn
    with get_conn() as conn:
        rows = conn.execute("SELECT id, name FROM locations").fetchall()  # list of dicts

The block commits when it exits cleanly and rolls back if it raises.

Apply the schema and seeds (no psql needed):
    uv run python -m app.db db/schema.sql db/seed/seed_locations.sql db/seed/seed_sd_places.sql
"""
from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from app.config import settings


@contextmanager
def get_conn(url: str | None = None) -> Iterator[psycopg.Connection]:
    """Yield a psycopg connection with dict rows, autocommit off.

    `url` defaults to settings.database_url (Neon main). The crawler passes a branch
    connection string here.
    """
    dsn = url or settings.database_url
    if not dsn:
        raise RuntimeError("DATABASE_URL is not set. Copy .env.example to .env and fill it in.")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        yield conn


def apply_sql_files(paths: list[str], url: str | None = None) -> None:
    """Run each SQL file in order, each in its own transaction."""
    for path in paths:
        sql = Path(path).read_text()
        with get_conn(url) as conn:
            conn.execute(sql)
        print(f"applied {path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python -m app.db <file.sql> [<file.sql> ...]")
    apply_sql_files(sys.argv[1:])
