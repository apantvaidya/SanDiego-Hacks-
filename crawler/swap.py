"""Swap validated locations from a crawl branch into main, in one transaction.

People texting during the swap keep seeing the old rows until the commit. Only `locations`
moves; users, profiles, conversations and follow-ups in main are never touched (that's why we
copy rows instead of promoting the branch). The previous rows stay in `locations_prev` for
rollback().
"""
from __future__ import annotations

from psycopg.types.json import Jsonb

COLUMNS = ("id", "name", "kind", "address", "phone", "hours", "has_xray", "walk_in", "accepts",
           "self_pay_price", "sliding_scale", "charity_care", "source_url", "crawled_at")
JSON_COLUMNS = {"hours", "accepts", "sliding_scale", "charity_care"}


def read_rows(conn) -> list[dict]:
    return conn.execute(
        f"SELECT {', '.join(COLUMNS)}, ST_AsEWKT(geom) AS geom_ewkt FROM locations ORDER BY id"
    ).fetchall()


def _params(row: dict) -> dict:
    out = {c: (Jsonb(row[c]) if c in JSON_COLUMNS and row[c] is not None else row[c]) for c in COLUMNS}
    out["geom_ewkt"] = row["geom_ewkt"]
    return out


_ALL = ", ".join((*COLUMNS, "geom"))


def swap_into_main(branch_conn, main_conn) -> int:
    """Copy main.locations to locations_prev, replace main.locations with the branch rows,
    commit. Returns the new row count. Raises (and main is unchanged) on any error."""
    rows = read_rows(branch_conn)
    if not rows:
        raise ValueError("refusing to swap in an empty locations table")
    insert = (
        f"INSERT INTO locations ({', '.join(COLUMNS)}, geom) VALUES ("
        + ", ".join(f"%({c})s" for c in COLUMNS)
        + ", ST_GeogFromText(%(geom_ewkt)s))"
    )
    with main_conn.transaction():
        main_conn.execute("DELETE FROM locations_prev")
        main_conn.execute(f"INSERT INTO locations_prev ({_ALL}) SELECT {_ALL} FROM locations")
        main_conn.execute("DELETE FROM locations")
        with main_conn.cursor() as cur:
            cur.executemany(insert, [_params(r) for r in rows])
    return len(rows)


def rollback(main_conn) -> int:
    """Restore locations from locations_prev. Returns the restored row count."""
    with main_conn.transaction():
        n = main_conn.execute("SELECT count(*) AS n FROM locations_prev").fetchone()["n"]
        if n == 0:
            raise ValueError("locations_prev is empty; nothing to roll back to")
        main_conn.execute("DELETE FROM locations")
        main_conn.execute(f"INSERT INTO locations ({_ALL}) SELECT {_ALL} FROM locations_prev")
    return n
