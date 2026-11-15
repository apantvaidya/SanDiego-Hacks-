"""Extract plan benefits (copays, nurse line, telehealth, deductible) from benefit summary PDFs. Owned by #14.

    uv run python -m crawler.benefits.extract_plans            # fetch + extract, print, write nothing
    uv run python -m crawler.benefits.extract_plans --apply    # also upsert into plans (DATABASE_URL)

Fetching: through Bright Data Web Unlocker when BRIGHTDATA_UNLOCKER_ZONE is set, otherwise a
direct HTTPS download (the sources are public PDFs). The run prints which was used.

Extraction: Gemini reads the PDF and returns the plan's in-network copays. It must return null
for anything not printed; code then rejects negative or absurd amounts. Nothing is guessed.

Sources (checked 2026-11-15):
  kaiser_cc_silver: Covered California "2026 Patient-Centered Benefit Plan Designs" (Final
    Approved July 28, 2025), the "Individual-only Silver Plan" design on page 6. Every
    individual Covered California Silver 70 plan, Kaiser's included, uses this standard
    design. Enrollees under 250% FPL are moved to enhanced Silver 73/87/94 designs with lower
    cost sharing, so these are the Silver 70 amounts, not every Silver member's.
    Nurse line: Kaiser Southern California advice nurse number on kp.org facility pages.
  chg_medi_cal: nurse line only, from chgsd.com ("Telephone Advice Nurse Line (TAN)
    1-800-647-6966"). Copays are left NULL here: cost.py shows Medi-Cal as $0 from its own
    rule, and that rule still needs confirming against current DHCS policy before the demo.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.llm.gemini import GeminiError, generate_json

log = logging.getLogger(__name__)

USER_AGENT = "care-navigator-hackathon"
MAX_REASONABLE_COPAY = 2_000
MAX_REASONABLE_DEDUCTIBLE = 20_000
GEMINI_PDF_TIMEOUT_S = 120  # a whole plan-design PDF takes longer than the webhook's 12 s

PLAN_SOURCES: list[dict] = [
    {
        "id": "kaiser_cc_silver",
        "insurer": "Kaiser Permanente",
        "plan_name": "Covered California Silver 70 (Kaiser Permanente HMO)",
        "coverage_type": "kaiser",
        "pdf_url": "https://www.coveredca.com/pdfs/2026%20Standard%20Benefit%20Plan%20Designs%20-%20Updated%20July%202025.pdf",
        "hint": ('Use only the "Individual-only Silver Plan" design (Silver 70, actuarial value about 70%), '
                 "on page 6. Ignore Platinum, Gold, Bronze, Catastrophic, HDHP, CCSB-only, and the "
                 "Silver 73 / 87 / 94 cost-sharing reduction designs."),
        "nurse_line": "1-833-574-2273",
    },
    {
        "id": "chg_medi_cal",
        "insurer": "Community Health Group",
        "plan_name": "Medi-Cal",
        "coverage_type": "medi_cal",
        "pdf_url": None,  # no copay extraction; see module docstring
        "source_url": "https://chgsd.com/members/materials-and-resources/member-emergency-preparedness-plan",
        "nurse_line": "1-800-647-6966",
    },
]

EXTRACT_SYSTEM = """\
You read a US health plan benefit document and return the member's in-network cost for a few
services, for one specific plan design. Return null for anything the document does not state
for that design. Never estimate. Amounts in dollars, numbers only.
- urgent_care_copay: urgent care visit copay
- er_copay: emergency room facility copay (not the physician fee)
- pcp_copay: primary care visit to treat an injury, illness, or condition
- telehealth: "free", "copay", "none" or "unknown"; telehealth_copay if a dollar copay is listed
- deductible: individual medical deductible (null if none or not stated)
- deductible_applies_to_urgent_care: true/false/null
- evidence: one short quote or cell text per number you returned"""

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "urgent_care_copay": {"type": ["number", "null"]},
        "er_copay": {"type": ["number", "null"]},
        "pcp_copay": {"type": ["number", "null"]},
        "telehealth": {"type": "string", "enum": ["free", "copay", "none", "unknown"]},
        "telehealth_copay": {"type": ["number", "null"]},
        "deductible": {"type": ["number", "null"]},
        "deductible_applies_to_urgent_care": {"type": ["boolean", "null"]},
        "evidence": {"type": "string"},
    },
    "required": ["urgent_care_copay", "er_copay", "pcp_copay", "telehealth", "telehealth_copay",
                 "deductible", "deductible_applies_to_urgent_care", "evidence"],
}


def fetch(url: str) -> tuple[bytes, str]:
    """Return (body, via). Bright Data Web Unlocker when a zone is configured, else direct."""
    if settings.brightdata_api_token and settings.brightdata_unlocker_zone:
        resp = httpx.post(
            "https://api.brightdata.com/request",
            headers={"Authorization": f"Bearer {settings.brightdata_api_token}"},
            json={"zone": settings.brightdata_unlocker_zone, "url": url, "format": "raw"},
            timeout=120,
        )
        resp.raise_for_status()
        return resp.content, "bright_data"
    resp = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=120, follow_redirects=True)
    resp.raise_for_status()
    return resp.content, "direct"


def _money(v, cap: float) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if 0 <= v <= cap else None


def clean(out: dict) -> dict:
    """Keep only sane values from Gemini's answer. Anything odd becomes NULL, never a guess."""
    tele = out.get("telehealth") if out.get("telehealth") in ("free", "copay", "none") else "unknown"
    tele_copay = _money(out.get("telehealth_copay"), MAX_REASONABLE_COPAY)
    if tele == "free":
        tele_copay = 0.0
    return {
        "urgent_care_copay": _money(out.get("urgent_care_copay"), MAX_REASONABLE_COPAY),
        "er_copay": _money(out.get("er_copay"), MAX_REASONABLE_COPAY),
        "pcp_copay": _money(out.get("pcp_copay"), MAX_REASONABLE_COPAY),
        "telehealth": tele,
        "telehealth_copay": tele_copay,
        "deductible": _money(out.get("deductible"), MAX_REASONABLE_DEDUCTIBLE),
    }


def extract_plan(pdf_url: str, hint: str = "") -> dict:
    """Fetch the PDF and extract `plans` columns with Gemini. Adds source_url, extracted_at,
    fetched_via and the model's evidence text (for the run log, not stored)."""
    body, via = fetch(pdf_url)
    if not body.startswith(b"%PDF-"):
        raise ValueError(f"{pdf_url} did not return a PDF ({via})")
    try:
        out = generate_json(prompt=f"Plan design to read: {hint or 'the plan in this document'}",
                            schema=EXTRACT_SCHEMA, image=body, system=EXTRACT_SYSTEM,
                            timeout_s=GEMINI_PDF_TIMEOUT_S)
    except GeminiError as e:
        raise RuntimeError(f"Gemini could not read {pdf_url}: {e}") from e
    return {**clean(out), "source_url": pdf_url, "extracted_at": datetime.now(timezone.utc),
            "fetched_via": via, "evidence": str(out.get("evidence") or "")[:500]}


def build_rows() -> list[dict]:
    rows = []
    for src in PLAN_SOURCES:
        row = {k: src[k] for k in ("id", "insurer", "plan_name", "coverage_type", "nurse_line")}
        if src.get("pdf_url"):
            row.update(extract_plan(src["pdf_url"], src.get("hint", "")))
        else:
            row.update({"source_url": src.get("source_url"), "extracted_at": datetime.now(timezone.utc),
                        "fetched_via": None, "evidence": "nurse line only"})
        rows.append(row)
    return rows


_COPAY_COLUMNS = ("urgent_care_copay", "er_copay", "pcp_copay", "telehealth", "telehealth_copay", "deductible")


def apply(rows: list[dict], url: str | None = None) -> int:
    """Upsert by stable id. Rows without extracted copays (chg_medi_cal) only update the
    nurse line, names and source, leaving copay columns as they are."""
    from app.db import get_conn

    with get_conn(url) as conn:
        for r in rows:
            has_copays = "urgent_care_copay" in r
            cols = ["id", "insurer", "plan_name", "coverage_type", "nurse_line", "source_url", "extracted_at"]
            if has_copays:
                cols += list(_COPAY_COLUMNS)
            updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "id")
            conn.execute(
                f"INSERT INTO plans ({', '.join(cols)}) VALUES ({', '.join('%s' for _ in cols)}) "
                f"ON CONFLICT (id) DO UPDATE SET {updates}",
                [r.get(c) for c in cols],
            )
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract plan benefits into the plans table")
    parser.add_argument("--apply", action="store_true", help="upsert into DATABASE_URL")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    rows = build_rows()
    for r in rows:
        print(json.dumps({k: v for k, v in r.items() if k != "extracted_at"}, indent=1, default=str))
    if args.apply:
        print(f"upserted {apply(rows)} plans")
    return 0


if __name__ == "__main__":
    sys.exit(main())
