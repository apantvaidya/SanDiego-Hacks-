"""Tests for #14 plan extraction: fetch and Gemini are faked."""
import pytest

from app.llm.gemini import GeminiError
from crawler.benefits import extract_plans as ep
from crawler.benefits.extract_plans import PLAN_SOURCES, build_rows, clean, extract_plan


@pytest.fixture
def fake_pdf(monkeypatch):
    monkeypatch.setattr(ep, "fetch", lambda url: (b"%PDF-1.7 fake", "direct"))


def test_clean_keeps_sane_values_and_nulls_the_rest():
    out = clean({"urgent_care_copay": 50, "er_copay": 400, "pcp_copay": -5, "telehealth": "free",
                 "telehealth_copay": None, "deductible": 999_999})
    assert out == {"urgent_care_copay": 50.0, "er_copay": 400.0, "pcp_copay": None, "telehealth": "free",
                   "telehealth_copay": 0.0, "deductible": None}


def test_clean_rejects_strings_bools_and_unknown_telehealth():
    out = clean({"urgent_care_copay": "$50", "er_copay": True, "pcp_copay": None, "telehealth": "maybe",
                 "telehealth_copay": 20, "deductible": 5400})
    assert out["urgent_care_copay"] is None and out["er_copay"] is None
    assert out["telehealth"] == "unknown" and out["telehealth_copay"] == 20.0 and out["deductible"] == 5400.0


def test_extract_plan_passes_pdf_and_hint(monkeypatch, fake_pdf):
    seen = {}

    def fake(prompt, schema, image=None, system=None):
        seen.update(prompt=prompt, image=image, system=system)
        return {"urgent_care_copay": 50, "er_copay": 400, "pcp_copay": 50, "telehealth": "copay",
                "telehealth_copay": 50, "deductible": 5400, "deductible_applies_to_urgent_care": False,
                "evidence": "Urgent care $50"}

    monkeypatch.setattr(ep, "generate_json", fake)
    row = extract_plan("https://example.org/sbc.pdf", hint="Individual-only Silver Plan")
    assert seen["image"].startswith(b"%PDF-") and "Individual-only Silver Plan" in seen["prompt"]
    assert "Never estimate" in seen["system"]
    assert row["urgent_care_copay"] == 50 and row["source_url"] == "https://example.org/sbc.pdf"
    assert row["fetched_via"] == "direct" and row["extracted_at"] is not None


def test_extract_plan_rejects_non_pdf(monkeypatch):
    monkeypatch.setattr(ep, "fetch", lambda url: (b"<html>blocked</html>", "direct"))
    with pytest.raises(ValueError):
        extract_plan("https://example.org/x")


def test_extract_plan_gemini_failure_raises(monkeypatch, fake_pdf):
    monkeypatch.setattr(ep, "generate_json", lambda **k: (_ for _ in ()).throw(GeminiError("429")))
    with pytest.raises(RuntimeError):
        extract_plan("https://example.org/sbc.pdf")


def test_build_rows_chg_is_nurse_line_only(monkeypatch, fake_pdf):
    monkeypatch.setattr(ep, "generate_json", lambda **k: {"urgent_care_copay": 50, "er_copay": None,
                                                          "pcp_copay": None, "telehealth": "unknown",
                                                          "telehealth_copay": None, "deductible": None,
                                                          "evidence": ""})
    rows = {r["id"]: r for r in build_rows()}
    assert rows["kaiser_cc_silver"]["urgent_care_copay"] == 50
    assert rows["kaiser_cc_silver"]["nurse_line"] == "1-833-574-2273"
    chg = rows["chg_medi_cal"]
    assert chg["nurse_line"] == "1-800-647-6966" and "urgent_care_copay" not in chg
    assert chg["source_url"].startswith("https://chgsd.com/")


def test_fetch_uses_bright_data_when_zone_set(monkeypatch):
    calls = {}

    class Resp:
        content = b"%PDF-1.7"

        def raise_for_status(self):
            pass

    def fake_post(url, headers, json, timeout):
        calls.update(url=url, json=json)
        return Resp()

    monkeypatch.setattr(ep.settings, "brightdata_api_token", "tok")
    monkeypatch.setattr(ep.settings, "brightdata_unlocker_zone", "care_unlocker")
    monkeypatch.setattr(ep.httpx, "post", fake_post)
    body, via = ep.fetch("https://example.org/a.pdf")
    assert via == "bright_data" and calls["json"]["zone"] == "care_unlocker"


def test_sources_are_stable_ids():
    assert [s["id"] for s in PLAN_SOURCES] == ["kaiser_cc_silver", "chg_medi_cal"]
