"""Render option cards from DB facts. The LLM never writes these facts. Owned by #7.

Every fact in the text (name, distance, hours, X-ray, cost, address, phone, link, freshness)
comes from the Location row, the CostEstimate (#8) or the Verification (#15). The only model
text is the short reason, already cleaned by rank().

Freshness (CLAUDE.md rule 7): the #1 option says "verified X min ago" only when it carries a
live Verification; every other option says "checked <crawl time>".
"""
from __future__ import annotations

import logging
from datetime import datetime, time
from zoneinfo import ZoneInfo

from app.care.find_care import closes_at
from app.i18n.templates import t
from app.models import CostEstimate, Profile, RenderedOption

log = logging.getLogger(__name__)

SD_TZ = ZoneInfo("America/Los_Angeles")
_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_MONTHS_ES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def _lang(language: str | None) -> str:
    return "es" if (language or "en").lower().startswith("es") else "en"


def money(amount: float) -> str:
    return f"${amount:,.0f}" if float(amount).is_integer() else f"${amount:,.2f}"


def _clock(tm: time, lang: str) -> str:
    if tm == time(23, 59):
        return "midnight" if lang == "en" else "medianoche"
    h = tm.hour % 12 or 12
    mins = f":{tm.minute:02d}" if tm.minute else ""
    return f"{h}{mins} {'AM' if tm.hour < 12 else 'PM'}"


def _when(ts: datetime, lang: str) -> str:
    local = ts.astimezone(SD_TZ)
    clock = _clock(local.time().replace(second=0, microsecond=0), lang)
    if lang == "es":
        return f"{local.day} {_MONTHS_ES[local.month - 1]}, {clock}"
    return f"{local:%b} {local.day}, {clock}"


def format_cost(cost: CostEstimate | None, lang: str) -> str:
    """A plan-copay note ("estimated from your plan's summary") is shown only next to a
    number; other notes (charity care, X-ray extra) always show."""
    has_amount = cost is not None and (cost.low is not None or cost.high is not None)
    show_note = cost is not None and bool(cost.note) and (has_amount or cost.basis != "plan_copay")
    if cost is None or cost.basis == "unknown":
        text = t("cost.unknown", lang)
    elif cost.basis == "medi_cal":
        text = t("cost.medi_cal", lang)
    else:
        amount = None
        if cost.low is not None and cost.high is not None and cost.low != cost.high:
            amount = t("cost.range", lang, low=money(cost.low), high=money(cost.high))
        elif cost.low is not None or cost.high is not None:
            amount = money(cost.low if cost.low is not None else cost.high)
        if cost.basis == "plan_copay":
            text = t("cost.copay", lang, amount=amount) if amount else t("cost.copay_unknown", lang)
        elif cost.basis == "sliding_scale":
            text = t("cost.sliding_scale", lang, amount=amount) if amount else t("cost.sliding_scale_unknown", lang)
        elif cost.basis == "self_pay":
            text = t("cost.self_pay", lang, amount=amount) if amount else t("cost.unknown", lang)
        else:
            text = amount or t("cost.unknown", lang)
    if show_note:
        text = f"{text} ({cost.note})"
    return text


def _hours_line(hours: dict, now: datetime, lang: str) -> str | None:
    today = (hours or {}).get(_DAYS[now.weekday()], [])
    if today == [["00:00", "24:00"]]:
        return t("render.open_24h", lang)
    closing = closes_at(hours, now)
    return t("render.open_until", lang, time=_clock(closing, lang)) if closing else None


def _source_line(opt: RenderedOption, rank_index: int, now: datetime, lang: str) -> str:
    loc, ver = opt.location, opt.verification
    if rank_index == 0 and ver is not None and ver.open_now is not None:
        minutes = max(0, int((now - ver.checked_at).total_seconds() // 60))
        key = "render.source_verified" if ver.open_now else "render.source_verified_closed"
        return t(key, lang, url=loc.source_url, minutes=minutes)
    return t("render.source_checked", lang, url=loc.source_url, when=_when(loc.crawled_at, lang))


def _card(opt: RenderedOption, i: int, now: datetime, lang: str) -> str:
    loc = opt.location
    miles = f"{loc.distance_miles:.1f}" if loc.distance_miles is not None else "?"
    lines = [t("render.option_title", lang, n=i + 1, name=loc.name, miles=miles)]

    facts = [_hours_line(loc.hours, now, lang)]
    if loc.walk_in:
        facts.append(t("render.walk_in", lang))
    if loc.kind != "er":
        facts.append({True: t("render.xray_yes", lang), False: t("render.xray_no", lang),
                      None: t("render.xray_unknown", lang)}[loc.has_xray])
    lines.append(". ".join(f for f in facts if f) + ".")

    lines.append(t("render.cost", lang, cost=format_cost(opt.cost, lang)))
    if opt.reason:
        lines.append(t("render.why", lang, reason=opt.reason))
    lines.append(t("render.contact", lang, address=loc.address, phone=loc.phone) if loc.phone
                 else t("render.contact_no_phone", lang, address=loc.address))
    lines.append(_source_line(opt, i, now, lang))
    return "\n".join(lines)


def render_options(options: list[RenderedOption], language: str, *, now: datetime | None = None) -> str:
    """Short iMessage text: header, up to 3 cards, tradeoff note for uninsured, ER note."""
    lang = _lang(language)
    now = (now or datetime.now(SD_TZ)).astimezone(SD_TZ)
    parts = [t("render.header", lang)]
    parts += [_card(opt, i, now, lang) for i, opt in enumerate(options)]

    kinds = {o.location.kind for o in options}
    bases = {o.cost.basis for o in options if o.cost}
    if {"fqhc", "urgent_care"} <= kinds and bases & {"sliding_scale", "self_pay", "unknown"} and "medi_cal" not in bases:
        parts.append(t("render.tradeoff_uninsured", lang))
    if "er" not in kinds:
        parts.append(t("render.er_note", lang))
    return "\n\n".join(parts)


def _plan_info(plan_id: str | None) -> dict | None:
    if not plan_id:
        return None
    try:
        from app.db import get_conn
        with get_conn() as conn:
            return conn.execute("SELECT nurse_line, telehealth FROM plans WHERE id = %s", (plan_id,)).fetchone()
    except Exception:  # the fallback text must still go out
        log.exception("render_fallback: plan lookup failed")
        return None


def render_fallback(profile: Profile, language: str) -> str:
    """No verified options: plan nurse/advice line if known, 211 San Diego, telehealth if covered."""
    lang = _lang(language)
    lines = [t("fallback.none", lang)]
    plan = _plan_info(profile.plan_id)
    if plan and plan.get("nurse_line"):
        lines.append(t("fallback.nurse_line", lang, phone=plan["nurse_line"]))
    if plan and plan.get("telehealth") in ("free", "copay"):
        lines.append(t("fallback.telehealth", lang))
    lines.append(t("fallback.211", lang))
    lines.append(t("fallback.911", lang))
    return "\n".join(lines)
