"""Tests for #7 rank(): Gemini is mocked; code rules are what is tested."""
import uuid
from datetime import datetime, timezone

import pytest

from app.care import rank as rank_mod
from app.care.rank import MAX_OPTIONS, rank
from app.llm.gemini import GeminiError
from app.models import CareLevel, CoverageType, FplRange, Location, Profile


def loc(id, kind, miles, **kw):
    base = dict(id=id, name=id.replace("_", " ").title(), kind=kind, lat=32.8, lon=-117.2,
                address="1 Main St", phone=None, hours={}, has_xray=None, walk_in=True, accepts=[],
                self_pay_price=None, sliding_scale=None, source_url="https://example.org",
                crawled_at=datetime(2026, 11, 15, 8, 30, tzinfo=timezone.utc), distance_miles=miles)
    base.update(kw)
    return Location(**base)


def profile(cov, lang="en"):
    return Profile(id=uuid.uuid4(), user_id=uuid.uuid4(), label=cov.value, coverage_type=cov,
                   income_range_fpl=FplRange.UNDER_138 if cov is CoverageType.UNINSURED else None,
                   language=lang)


CANDS = [
    loc("afc_clairemont", "urgent_care", 5.1, has_xray=True),
    loc("afc_mira_mesa", "urgent_care", 5.5, has_xray=True, self_pay_price=199),
    loc("other_uc", "urgent_care", 6.0, has_xray=True),
    loc("fhcsd_logan_heights", "fqhc", 12.9),
]


def mock(monkeypatch, out=None, exc=None):
    calls = []

    def fake(prompt, schema, image=None, system=None):
        calls.append(prompt)
        if exc:
            raise exc
        return out

    monkeypatch.setattr(rank_mod, "generate_json", fake)
    return calls


def test_unknown_and_duplicate_ids_dropped(monkeypatch):
    mock(monkeypatch, {"ranked": [{"id": "made_up", "reason": "x"}, {"id": "afc_mira_mesa", "reason": "Has X-ray"},
                                  {"id": "afc_mira_mesa", "reason": "again"}]})
    out = rank(CANDS[:3], profile(CoverageType.KAISER), "ankle", CareLevel.URGENT_CARE)
    assert [o.location_id for o in out] == ["afc_mira_mesa"]
    assert out[0].reason == "Has X-ray"


def test_capped_at_three(monkeypatch):
    mock(monkeypatch, {"ranked": [{"id": c.id, "reason": "ok"} for c in CANDS]})
    assert len(rank(CANDS, profile(CoverageType.MEDI_CAL), "ankle", CareLevel.URGENT_CARE)) == MAX_OPTIONS


@pytest.mark.parametrize("reason", ["Only $199", "Open until 8 pm", "Closes at 20:00", "2 miles away"])
def test_reasons_with_facts_replaced(monkeypatch, reason):
    mock(monkeypatch, {"ranked": [{"id": "afc_mira_mesa", "reason": reason}]})
    out = rank(CANDS[:3], profile(CoverageType.KAISER), "ankle", CareLevel.URGENT_CARE)
    assert out[0].reason == "Urgent care that takes walk-ins"


def test_spanish_default_reason(monkeypatch):
    mock(monkeypatch, {"ranked": [{"id": "fhcsd_logan_heights", "reason": "$0"}]})
    out = rank(CANDS[3:], profile(CoverageType.MEDI_CAL, "es"), "tobillo", CareLevel.URGENT_CARE)
    assert out[0].reason.startswith("Clínica comunitaria")


def test_gemini_down_ranks_by_distance(monkeypatch):
    mock(monkeypatch, exc=GeminiError("503"))
    out = rank(CANDS[:3], profile(CoverageType.KAISER), "ankle", CareLevel.URGENT_CARE)
    assert [o.location_id for o in out] == ["afc_clairemont", "afc_mira_mesa", "other_uc"]


def test_uninsured_tradeoff_adds_clinic_when_full(monkeypatch):
    mock(monkeypatch, {"ranked": [{"id": "afc_clairemont", "reason": "a"}, {"id": "afc_mira_mesa", "reason": "b"},
                                  {"id": "other_uc", "reason": "c"}]})
    out = rank(CANDS, profile(CoverageType.UNINSURED), "ankle", CareLevel.URGENT_CARE)
    kinds = {o.location_id for o in out}
    assert "fhcsd_logan_heights" in kinds and len(out) == 3
    assert "afc_clairemont" in kinds  # replaced the last duplicate kind, kept the top pick


def test_uninsured_tradeoff_adds_urgent_care(monkeypatch):
    mock(monkeypatch, {"ranked": [{"id": "fhcsd_logan_heights", "reason": "cheap"}]})
    out = rank(CANDS, profile(CoverageType.UNINSURED), "ankle", CareLevel.URGENT_CARE)
    assert [o.location_id for o in out] == ["fhcsd_logan_heights", "afc_clairemont"]


def test_tradeoff_not_forced_for_insured(monkeypatch):
    mock(monkeypatch, {"ranked": [{"id": "fhcsd_logan_heights", "reason": "in network"}]})
    out = rank(CANDS, profile(CoverageType.MEDI_CAL), "ankle", CareLevel.URGENT_CARE)
    assert [o.location_id for o in out] == ["fhcsd_logan_heights"]


def test_prompt_has_no_prices_or_names(monkeypatch):
    calls = mock(monkeypatch, {"ranked": []})
    rank(CANDS, profile(CoverageType.UNINSURED), "ankle", CareLevel.URGENT_CARE)
    assert "199" not in calls[0] and "1 Main St" not in calls[0]


def test_empty_candidates():
    assert rank([], profile(CoverageType.KAISER), "ankle", CareLevel.URGENT_CARE) == []
