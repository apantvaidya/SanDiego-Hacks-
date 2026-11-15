"""Tests for #8 estimate_cost: one per branch. Plans are passed in, so no DB needed
(one test reads the seeded plans row when DATABASE_URL is set)."""
import uuid
from datetime import datetime, timezone

import pytest

from app.care.cost import NOTES, estimate_cost, get_plan
from app.care.render import format_cost
from app.config import settings
from app.models import CareLevel, CoverageType, FplRange, Location, Profile


def loc(kind, accepts, **kw):
    base = dict(id="x", name="X", kind=kind, lat=32.8, lon=-117.2, address="a", phone=None, hours={},
                has_xray=None, walk_in=True, accepts=accepts, self_pay_price=None, sliding_scale=None,
                source_url="u", crawled_at=datetime(2026, 11, 15, tzinfo=timezone.utc))
    base.update(kw)
    return Location(**base)


def prof(cov, plan_id=None, income=None, lang="en"):
    return Profile(id=uuid.uuid4(), user_id=uuid.uuid4(), label=cov.value, coverage_type=cov,
                   plan_id=plan_id, income_range_fpl=income, language=lang)


KAISER_PLAN = {"id": "kaiser_cc_silver", "urgent_care_copay": 50, "er_copay": 400, "pcp_copay": 45,
               "telehealth": "free", "telehealth_copay": None, "source_url": "https://example.org/sbc.pdf"}
PLACEHOLDER_PLAN = {**KAISER_PLAN, "urgent_care_copay": None, "source_url": None}
SCALE = {"<138": [0, 30], "138-200": [30, 60], "200-400": [60, 120]}
UC = CareLevel.URGENT_CARE


# ------------------------------------------------------------------ Medi-Cal

def test_medi_cal_in_network_is_zero():
    c = estimate_cost(loc("fqhc", ["chg_medi_cal"]), prof(CoverageType.MEDI_CAL, "chg_medi_cal"), UC)
    assert (c.low, c.high, c.basis) == (0, 0, "medi_cal")


def test_medi_cal_other_plan_unknown():
    c = estimate_cost(loc("fqhc", ["other_medi_cal_plan"]), prof(CoverageType.MEDI_CAL, "chg_medi_cal"), UC)
    assert c.basis == "unknown"


def test_medi_cal_er_zero():
    er = loc("er", ["uninsured", "medi_cal", "chg_medi_cal", "kaiser"])
    assert estimate_cost(er, prof(CoverageType.MEDI_CAL, "chg_medi_cal"), CareLevel.ER).basis == "medi_cal"


# ------------------------------------------------------------------ plan copays

@pytest.mark.parametrize("kind,level,expected", [
    ("kaiser", CareLevel.URGENT_CARE, 50),
    ("er", CareLevel.ER, 400),
    ("kaiser", CareLevel.PRIMARY_CARE, 45),
    ("telehealth", CareLevel.TELEHEALTH, 0),
])
def test_plan_copay_by_kind_and_level(kind, level, expected):
    c = estimate_cost(loc(kind, ["kaiser"]), prof(CoverageType.KAISER, "kaiser_cc_silver"), level, plan=KAISER_PLAN)
    assert (c.low, c.high, c.basis) == (expected, expected, "plan_copay")
    assert c.note == NOTES["en"]["plan_summary"]


def test_placeholder_plan_has_no_amount_and_no_note():
    c = estimate_cost(loc("kaiser", ["kaiser"]), prof(CoverageType.KAISER, "kaiser_cc_silver"), UC, plan=PLACEHOLDER_PLAN)
    assert (c.low, c.high, c.basis, c.note) == (None, None, "plan_copay", None)
    assert format_cost(c, "en") == "your plan's copay (amount not confirmed)"


def test_spanish_plan_note():
    c = estimate_cost(loc("kaiser", ["kaiser"]), prof(CoverageType.KAISER, "kaiser_cc_silver", lang="es"), UC,
                      plan=KAISER_PLAN)
    assert format_cost(c, "es") == "copago de $50 (estimado según el resumen de tu plan)"


# ------------------------------------------------------------------ uninsured

def test_sliding_scale_for_income():
    c = estimate_cost(loc("fqhc", ["uninsured"], sliding_scale=SCALE),
                      prof(CoverageType.UNINSURED, income=FplRange.FROM_138_TO_200), UC)
    assert (c.low, c.high, c.basis, c.note) == (30, 60, "sliding_scale", None)


def test_sliding_scale_unknown_income_full_range_and_note():
    c = estimate_cost(loc("fqhc", ["uninsured"], sliding_scale=SCALE), prof(CoverageType.UNINSURED), UC)
    assert (c.low, c.high) == (0, 120)
    assert c.note == NOTES["en"]["income_unlocks"]


def test_fqhc_without_scale_data():
    c = estimate_cost(loc("fqhc", ["uninsured"]), prof(CoverageType.UNINSURED, income=FplRange.UNDER_138), UC)
    assert (c.low, c.high, c.basis) == (None, None, "sliding_scale")
    assert format_cost(c, "en") == "sliding fee based on your income"


def test_self_pay_with_xray_note():
    c = estimate_cost(loc("urgent_care", ["uninsured"], self_pay_price=199, has_xray=True),
                      prof(CoverageType.UNINSURED), UC)
    assert (c.low, c.high, c.basis) == (199, 199, "self_pay")
    assert format_cost(c, "en") == "$199 self-pay (visit only, X-ray costs extra)"


def test_self_pay_unknown_price():
    c = estimate_cost(loc("urgent_care", ["uninsured"]), prof(CoverageType.UNINSURED), UC)
    assert c.basis == "unknown" and c.low is None


@pytest.mark.parametrize("income", [None, FplRange.UNDER_138, FplRange.FROM_200_TO_400])
def test_uninsured_er_no_number_charity_note(income):
    c = estimate_cost(loc("er", ["uninsured"]), prof(CoverageType.UNINSURED, income=income), CareLevel.ER)
    assert c.low is None and c.high is None
    assert "4 times the poverty line" in c.note
    assert "financial assistance" in format_cost(c, "en")  # note shows even without a number


def test_uninsured_er_over_400_no_charity_note():
    c = estimate_cost(loc("er", ["uninsured"]), prof(CoverageType.UNINSURED, income=FplRange.OVER_400), CareLevel.ER)
    assert c.note is None and c.basis == "unknown"


def test_unknown_coverage():
    assert estimate_cost(loc("urgent_care", ["x"]), prof(CoverageType.COMMERCIAL), UC).basis == "plan_copay"


@pytest.mark.skipif(not settings.database_url, reason="DATABASE_URL not set")
def test_seeded_plans_lookup():
    assert get_plan("kaiser_cc_silver")["nurse_line"] == "1-833-574-2273"
    assert get_plan("no_such_plan") is None
