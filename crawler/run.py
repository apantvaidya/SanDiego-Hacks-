"""Crawl run: Neon branch -> load -> validate -> swap into main (or reject).

    uv run python -m crawler.run --chg               # branch, reload CHG data, validate, print; no swap
    uv run python -m crawler.run --chg --swap        # ... and swap into main if validation passes
    uv run python -m crawler.run --rollback          # restore main.locations from locations_prev
    uv run python -m crawler.run --keep-branch ...   # keep the branch as a snapshot (default: keep)

Every run is logged in main's crawl_runs (branch name, counts, checks, passed, swapped_at).
Branches are kept by default as snapshots, so two crawls can be compared later; pass
--delete-branch to remove it.

Loaders: --chg runs the CHG FHIR loader (#11) against the branch. The Bright Data crawl (#12)
gets a flag here once it merges.
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone

from psycopg.types.json import Jsonb

from app.db import get_conn
from crawler import branch as neon
from crawler.swap import rollback, swap_into_main
from crawler.validate import measure, problems

log = logging.getLogger(__name__)


def _log_run(name: str, m: dict, found: list[str], swapped: bool) -> None:
    with get_conn() as main:
        main.execute(
            """INSERT INTO crawl_runs (branch_name, finished_at, row_count, checks, passed, swapped_at)
               VALUES (%s, now(), %s, %s, %s, %s)""",
            (name, m["row_count"], Jsonb({**m, "problems": found}), not found,
             datetime.now(timezone.utc) if swapped else None),
        )


def run(load_chg: bool, swap: bool, delete_branch: bool) -> dict:
    name = neon.crawl_branch_name()
    branch_id, branch_url = neon.create_branch(name)
    try:
        with get_conn(branch_url) as b:
            if load_chg:
                from crawler.chg_fhir import fetch_chg_locations, load

                print("CHG:", load(fetch_chg_locations(), b))
        with get_conn(branch_url) as b, get_conn() as main:
            m = measure(b, main)
        found = problems(m)
        print(json.dumps({k: (v if not isinstance(v, list) else len(v)) for k, v in m.items()}, indent=1))
        swapped = False
        if found:
            print("REJECTED, main untouched:\n  " + "\n  ".join(found))
        elif swap:
            with get_conn(branch_url) as b, get_conn() as main:
                print("swapped rows into main:", swap_into_main(b, main))
            swapped = True
        else:
            print("validation passed; run with --swap to copy into main")
        _log_run(name, m, found, swapped)
        return {"branch": name, "problems": found, "swapped": swapped}
    finally:
        if delete_branch:
            neon.delete_branch(branch_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Crawl into a Neon branch, validate, swap into main")
    parser.add_argument("--chg", action="store_true", help="load the CHG FHIR directory into the branch")
    parser.add_argument("--swap", action="store_true", help="swap into main if validation passes")
    parser.add_argument("--delete-branch", action="store_true", help="delete the branch afterward")
    parser.add_argument("--rollback", action="store_true", help="restore main.locations from locations_prev")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.rollback:
        with get_conn() as main:
            print("restored rows:", rollback(main))
        return
    run(args.chg, args.swap, args.delete_branch)


if __name__ == "__main__":
    main()
