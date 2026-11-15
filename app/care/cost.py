"""Cost estimate for this user at this location. Pure logic plus plan lookup. Owned by #8.

estimate_cost(location, profile, level) -> CostEstimate, never a guess:
  * Medi-Cal, location takes the plan: $0 (basis "medi_cal")
  * Kaiser / commercial / Covered CA / Medicare with a plan_id: the plan's copay for this
    kind of visit (basis "plan_copay"). "estimated from your plan's summary" only when the
    plans row was extracted from a real summary (source_url set, #14). Copay unknown:
    low/high None, and render says "amount not confirmed".
  * Uninsured at a place with a sliding scale: the range for the income bracket. Unknown
    income: the full range, plus a note that sharing income can lower it.
  * Uninsured at urgent care with a self-pay price: the visit price; X-ray noted as extra.
  * Uninsured at an ER: no number. California hospitals must offer charity care or
    discounts at or below 400% FPL (AB 1020, HCAI), so that is noted unless income > 400%.
  * Anything else: basis "unknown".
Notes are written in the profile's language, because render shows them as-is.
"""
from __future__ import annotations

import logging
import time

from app.models import CareLevel, CostEstimate, CoverageType, FplRange, Location, Profile

log = logging.getLogger(__name__)

PLAN_CACHE_TTL_S = 300
_plan_cache: dict[str, tuple[float, dict | None]] = {}

NOTES = {
    "en": {
        "plan_summary": "estimated from your plan's summary",
        "income_unlocks": "sharing your income can lower it",
        "xray_extra": "visit only, X-ray costs extra",
        "charity_care": "California hospitals must offer free or discounted care if your income is up to 4 times the poverty line; ask for financial assistance",
    },
    "es": {
        "plan_summary": "estimado según el resumen de tu plan",
        "income_unlocks": "compartir tus ingresos puede bajarlo",
        "xray_extra": "solo la consulta, los rayos X cuestan aparte",
        "charity_care": "los hospitales de California deben ofrecer atención gratis o con descuento si tus ingresos son hasta 4 veces el nivel de pobreza; pide asistencia financiera",
    },
}

INSURED = {CoverageType.KAISER.value, CoverageType.COMMERCIAL.value, CoverageType.COVERED_CA.value,
           CoverageType.MEDICARE.value}


def _lang(profile: Profile) -> str:
    return "es" if (profile.language or "en").lower().startswith("es") else "en"


def _value(x) -> str | None:
    return getattr(x, "value", x)


def get_plan(plan_id: str | None) -> dict | None:
    """plans row as a dict, cached for a few minutes. None if missing or the DB is down."""
    if not plan_id:
        return None
    hit = _plan_cache.get(plan_id)
    if hit and time.monotonic() - hit[0] < PLAN_CACHE_TTL_S:
        return hit[1]
    try:
        from app.db import get_conn
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM plans WHERE id = %s", (plan_id,)).fetchone()
    except Exception:
        log.exception("get_plan(%s) failed", plan_id)
        return None
    _plan_cache[plan_id] = (time.monotonic(), row)
    return row


def _num(x) -> float | None:
    return float(x) if x is not None else None


def _copay_for(plan: dict, location: Location, level: CareLevel) -> float | None:
    if location.kind == "er" or level is CareLevel.ER:
        return _num(plan.get("er_copay"))
    if location.kind == "telehealth" or level is CareLevel.TELEHEALTH:
        tele = plan.get("telehealth")
        if tele == "free":
            return 0.0
        return _num(plan.get("telehealth_copay")) if tele == "copay" else None
    if level is CareLevel.URGENT_CARE or location.kind == "urgent_care":
        return _num(plan.get("urgent_care_copay"))
    return _num(plan.get("pcp_copay"))


def _bounds(v) -> tuple[float, float] | None:
    if isinstance(v, (int, float)):
        return float(v), float(v)
    if isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v):
        return float(v[0]), float(v[1])
    return None


def _sliding(location: Location, profile: Profile, lang: str) -> CostEstimate:
    scale = location.sliding_scale or {}
    income = _value(profile.income_range_fpl)
    if income and income in scale and _bounds(scale[income]):
        low, high = _bounds(scale[income])
        return CostEstimate(low, high, "sliding_scale")
    all_bounds = [b for b in (_bounds(v) for v in scale.values()) if b]
    if all_bounds:
        return CostEstimate(min(b[0] for b in all_bounds), max(b[1] for b in all_bounds), "sliding_scale",
                            NOTES[lang]["income_unlocks"] if not income else None)
    return CostEstimate(None, None, "sliding_scale")


def estimate_cost(location: Location, profile: Profile, level: CareLevel, *,
                  plan: dict | None = None) -> CostEstimate:
    """`plan` is for tests; normally the plans row is looked up by profile.plan_id."""
    lang = _lang(profile)
    coverage = _value(profile.coverage_type)
    accepts = set(location.accepts or [])

    if coverage == CoverageType.MEDI_CAL.value:
        if (profile.plan_id and profile.plan_id in accepts) or (not profile.plan_id and "medi_cal" in accepts):
            return CostEstimate(0, 0, "medi_cal")
        return CostEstimate(None, None, "unknown")

    if coverage in INSURED:
        plan = plan if plan is not None else get_plan(profile.plan_id)
        if not plan:
            return CostEstimate(None, None, "plan_copay")
        copay = _copay_for(plan, location, level)
        if copay is None:
            return CostEstimate(None, None, "plan_copay")
        note = NOTES[lang]["plan_summary"] if plan.get("source_url") else None
        return CostEstimate(copay, copay, "plan_copay", note)

    if coverage == CoverageType.UNINSURED.value:
        if location.kind == "er":
            if _value(profile.income_range_fpl) == FplRange.OVER_400.value:
                return CostEstimate(None, None, "unknown")
            return CostEstimate(None, None, "unknown", NOTES[lang]["charity_care"])
        if location.sliding_scale is not None or location.kind == "fqhc":
            return _sliding(location, profile, lang)
        if location.self_pay_price is not None:
            price = float(location.self_pay_price)
            note = NOTES[lang]["xray_extra"] if location.has_xray and level is not CareLevel.PRIMARY_CARE else None
            return CostEstimate(price, price, "self_pay", note)

    return CostEstimate(None, None, "unknown")
