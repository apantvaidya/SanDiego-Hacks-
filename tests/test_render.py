"""Tests for #7 render_options / render_fallback and the i18n t() helper."""
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.care import render as render_mod
from app.care.render import format_cost, render_fallback, render_options
from app.i18n.templates import TEMPLATES, t
from app.models import CostEstimate, CoverageType, Location, Profile, RenderedOption, Verification

SD = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 11, 18, 13, 0, tzinfo=SD)  # Wednesday 1 PM
CRAWLED = datetime(2026, 11, 15, 0, 30, tzinfo=SD)
DAILY_9_9 = {d: [["09:00", "21:00"]] for d in ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]}
WEEK_8_8 = {d: [["08:00", "20:00"]] for d in ["mon", "tue", "wed", "thu", "fri", "sat"]}
ALL_DAY = {d: [["00:00", "24:00"]] for d in ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]}


def loc(id, kind, miles, hours, **kw):
    base = dict(id=id, name=kw.pop("name", id), kind=kind, lat=32.8, lon=-117.2, address="1 Main St, San Diego",
                phone="(619) 555-0100", hours=hours, has_xray=None, walk_in=True, accepts=[],
                self_pay_price=None, sliding_scale=None, source_url=f"https://example.org/{id}",
                crawled_at=CRAWLED, distance_miles=miles)
    base.update(kw)
    return Location(**base)


def opt(location, cost, reason="Fits", verification=None):
    return RenderedOption(location=location, reason=reason, cost=cost, verification=verification)


def profile(cov, plan_id=None):
    return Profile(id=uuid.uuid4(), user_id=uuid.uuid4(), label=cov.value, coverage_type=cov, plan_id=plan_id)


KAISER_OPTS = [opt(loc("kaiser_sdmc_urgent", "kaiser", 7.0, DAILY_9_9, has_xray=True, name="Kaiser SDMC Urgent Care"),
                   CostEstimate(None, None, "plan_copay", "estimated from your plan's summary"),
                   verification=Verification("kaiser_sdmc_urgent", NOW - timedelta(minutes=2), True))]
MEDI_CAL_OPTS = [opt(loc("midway", "urgent_care", 8.9, {d: [["09:00", "14:00"]] for d in DAILY_9_9}), CostEstimate(0, 0, "medi_cal")),
                 opt(loc("fhcsd_logan_heights", "fqhc", 12.9, WEEK_8_8), CostEstimate(0, 0, "medi_cal"))]
UNINSURED_OPTS = [opt(loc("afc_mira_mesa", "urgent_care", 5.5, DAILY_9_9, has_xray=True), CostEstimate(199, 368, "self_pay")),
                  opt(loc("fhcsd_logan_heights", "fqhc", 12.9, WEEK_8_8), CostEstimate(None, None, "sliding_scale"))]


@pytest.mark.parametrize("opts", [KAISER_OPTS, MEDI_CAL_OPTS, UNINSURED_OPTS])
@pytest.mark.parametrize("lang", ["en", "es"])
def test_fits_about_two_bubbles(opts, lang):
    text = render_options(opts, lang, now=NOW)
    assert len(text) < 1100
    assert "—" not in text  # house style: no em dashes


def test_kaiser_card_facts_and_verified_label():
    text = render_options(KAISER_OPTS, "en", now=NOW)
    assert "1. Kaiser SDMC Urgent Care (7.0 mi)" in text
    assert "Open until 9 PM. Walk-in. X-ray on site." in text
    assert "Cost for you: your plan's copay (amount not confirmed)\n" in text  # no "estimated" note without a number
    assert "(verified 2 min ago)" in text
    assert "Go to the ER instead" in text


def test_only_first_option_can_say_verified():
    opts = [MEDI_CAL_OPTS[0], opt(MEDI_CAL_OPTS[1].location, CostEstimate(0, 0, "medi_cal"),
                                  verification=Verification("x", NOW, True))]
    text = render_options(opts, "en", now=NOW)
    assert "verified" not in text
    assert text.count("(checked Nov 15, 12:30 AM)") == 2


def test_live_check_says_closed():
    o = opt(loc("a", "urgent_care", 1.0, DAILY_9_9), CostEstimate(None, None, "unknown"),
            verification=Verification("a", NOW - timedelta(minutes=1), False))
    assert "may be closed, call first" in render_options([o], "en", now=NOW)


def test_medi_cal_zero_and_unknown_xray():
    text = render_options(MEDI_CAL_OPTS, "en", now=NOW)
    assert text.count("Cost for you: $0 with Medi-Cal") == 2
    assert "Open until 2 PM" in text and "Call ahead about X-ray" in text
    assert "tradeoff" not in text


def test_uninsured_tradeoff_and_ranges():
    text = render_options(UNINSURED_OPTS, "en", now=NOW)
    assert "$199 to $368 self-pay" in text
    assert "sliding fee based on your income" in text
    assert "The tradeoff:" in text


def test_spanish_render():
    text = render_options(UNINSURED_OPTS, "es", now=NOW)
    assert text.startswith("Opciones abiertas ahora:")
    assert "Abierto hasta las 9 PM" in text and "revisado 15 nov, 12:30 AM" in text
    assert "La diferencia:" in text


def test_er_open_24h_no_xray_line_no_er_note():
    o = opt(loc("ucsd_jacobs_er", "er", 0.4, ALL_DAY, has_xray=True), CostEstimate(None, None, "unknown"))
    text = render_options([o], "en", now=NOW)
    assert "Open 24 hours" in text and "X-ray" not in text and "Go to the ER instead" not in text


@pytest.mark.parametrize("cost,expected", [
    (CostEstimate(50, 50, "plan_copay"), "$50 copay"),
    (CostEstimate(0, 30, "sliding_scale"), "$0 to $30 on the sliding fee scale"),
    (CostEstimate(None, None, "unknown"), "not confirmed, ask when you call"),
    (None, "not confirmed, ask when you call"),
    (CostEstimate(12.5, None, "self_pay"), "$12.50 self-pay"),
    (CostEstimate(50, 50, "plan_copay", "estimated from your plan's summary"),
     "$50 copay (estimated from your plan's summary)"),
])
def test_format_cost(cost, expected):
    assert format_cost(cost, "en") == expected


def test_fallback_with_nurse_line_and_telehealth(monkeypatch):
    monkeypatch.setattr(render_mod, "_plan_info", lambda pid: {"nurse_line": "1-833-574-2273", "telehealth": "free"})
    text = render_fallback(profile(CoverageType.KAISER, "kaiser_cc_silver"), "en")
    assert "1-833-574-2273" in text and "telehealth" in text and "2-1-1" in text and "911" in text


def test_fallback_uninsured_spanish(monkeypatch):
    monkeypatch.setattr(render_mod, "_plan_info", lambda pid: None)
    text = render_fallback(profile(CoverageType.UNINSURED), "es")
    assert text.startswith("No tengo opciones verificadas") and "2-1-1" in text


def test_t_fallbacks():
    assert t("render.header", "es") == "Opciones abiertas ahora:"
    assert t("render.header", "fr") == "Options open now:"
    TEMPLATES["en"]["test.only_en"] = "hi {name}"
    try:
        assert t("test.only_en", "es", name="Ana") == "hi Ana"
    finally:
        del TEMPLATES["en"]["test.only_en"]
    with pytest.raises(KeyError):
        t("no.such.key", "en")


def test_every_spanish_key_has_english():
    assert set(TEMPLATES["es"]) <= set(TEMPLATES["en"])
    assert set(TEMPLATES["en"]) == set(TEMPLATES["es"])


@pytest.mark.parametrize("raw,shown", [("(877)-693-6266", "(877) 693-6266"), ("+18776936266", "(877) 693-6266"),
                                       ("1-833-574-2273", "(833) 574-2273"), ("858.900.3550", "(858) 900-3550"),
                                       ("(619) 555-0100 ext 2", "(619) 555-0100 ext 2"), ("211", "211")])
def test_format_phone(raw, shown):
    from app.care.render import format_phone
    assert format_phone(raw) == shown
