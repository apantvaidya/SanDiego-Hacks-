"""Neon branch per crawl, via the Neon API v2.

Load the crawl into a fresh branch, validate there (crawler/validate.py), then crawler/swap.py
copies the branch's `locations` into main in one transaction. NEVER promote the branch: a
branch is a copy of the whole database, so promoting one made at 1 AM would erase profiles and
conversations saved to main after 1 AM.

Needs NEON_API_KEY and NEON_PROJECT_ID. A new branch copies its parent (main) at creation,
roles included, so its connection string is main's DATABASE_URL with the new endpoint's host.

API (https://neon.com/docs/reference/api):
  POST   /projects/{project_id}/branches            {"branch": {"name"}, "endpoints": [{"type": "read_write"}]}
  GET    /projects/{project_id}/operations/{op_id}  wait until every operation is finished
  DELETE /projects/{project_id}/branches/{branch_id}
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse

import httpx

from app.config import settings

log = logging.getLogger(__name__)

API = "https://console.neon.tech/api/v2"
DONE = {"finished", "skipped", "cancelled"}
FAILED = {"failed", "error"}


class NeonError(RuntimeError):
    pass


def _client() -> httpx.Client:
    if not settings.neon_api_key or not settings.neon_project_id:
        raise NeonError("set NEON_API_KEY and NEON_PROJECT_ID in .env")
    return httpx.Client(
        base_url=API,
        headers={"Authorization": f"Bearer {settings.neon_api_key}", "Accept": "application/json"},
        timeout=60.0,
    )


def crawl_branch_name(now: datetime | None = None) -> str:
    """crawl-2026-11-15-0100 (UTC)."""
    now = now or datetime.now(timezone.utc)
    return now.strftime("crawl-%Y-%m-%d-%H%M")


def swap_host(url: str, host: str) -> str:
    """Main's connection string with another endpoint host (same role, password, database)."""
    parts = urlparse(url)
    netloc = parts.netloc
    creds = netloc.rsplit("@", 1)[0] + "@" if "@" in netloc else ""
    port = f":{parts.port}" if parts.port else ""
    return urlunparse(parts._replace(netloc=f"{creds}{host}{port}"))


def connection_string(created: dict, main_url: str) -> str:
    """Prefer the URI Neon returns; otherwise main's URL with the new endpoint host."""
    for item in created.get("connection_uris") or []:
        if item.get("connection_uri"):
            return item["connection_uri"]
    endpoints = created.get("endpoints") or []
    host = endpoints[0].get("host") if endpoints else None
    if not host or not main_url:
        raise NeonError("branch created but no endpoint host or connection URI in the response")
    return swap_host(main_url, host)


def _wait(client: httpx.Client, operations: list[dict], timeout_s: float = 120.0) -> None:
    pid = settings.neon_project_id
    pending = [op["id"] for op in operations if op.get("status") not in DONE]
    deadline = time.monotonic() + timeout_s
    while pending:
        if time.monotonic() > deadline:
            raise NeonError(f"Neon operations still running after {timeout_s:.0f}s: {pending}")
        time.sleep(1.5)
        still = []
        for op_id in pending:
            status = client.get(f"/projects/{pid}/operations/{op_id}").json()["operation"]["status"]
            if status in FAILED:
                raise NeonError(f"Neon operation {op_id} {status}")
            if status not in DONE:
                still.append(op_id)
        pending = still


def create_branch(name: str) -> tuple[str, str]:
    """Create a branch from the default branch with a read-write compute.
    Returns (branch_id, connection_string)."""
    with _client() as client:
        for attempt in range(5):
            resp = client.post(
                f"/projects/{settings.neon_project_id}/branches",
                json={"branch": {"name": name}, "endpoints": [{"type": "read_write"}]},
            )
            if resp.status_code == 423:  # locked by another operation; safe to retry
                time.sleep(2 * (attempt + 1))
                continue
            break
        if resp.status_code >= 400:
            raise NeonError(f"create branch {resp.status_code}: {resp.text[:300]}")
        created = resp.json()
        _wait(client, created.get("operations") or [])
    branch_id = created["branch"]["id"]
    log.info("created Neon branch %s (%s)", name, branch_id)
    return branch_id, connection_string(created, settings.database_url)


def delete_branch(branch_id: str) -> None:
    with _client() as client:
        resp = client.delete(f"/projects/{settings.neon_project_id}/branches/{branch_id}")
        if resp.status_code >= 400 and resp.status_code != 404:
            raise NeonError(f"delete branch {resp.status_code}: {resp.text[:300]}")
