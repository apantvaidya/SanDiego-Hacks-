"""Income brackets by household size from current HHS poverty guidelines. Owned by #10.

Source: HHS / ASPE, "2026 Poverty Guidelines: 48 Contiguous States (all states except
Alaska and Hawaii)", detailed-guidelines-2026.pdf, published in the Federal Register on
2026-01-15:
https://aspe.hhs.gov/sites/default/files/documents/b1bfa16b20ae9b89d525bc35de7c1643/detailed-guidelines-2026.pdf
100% of poverty: $15,960 for 1 person, plus $5,680 for each additional person
(1: 15,960 | 2: 21,640 | 3: 27,320 | 4: 33,000 | 5: 38,680 | 6: 44,360), checked against the
table on 2026-11-15. California uses the 48-state guidelines. Update both numbers each
January when HHS publishes new guidelines.

Brackets map to the FplRange tiers the rest of the app uses: <138 (Medi-Cal income limit),
138-200, 200-400 (hospital charity care / discount limit under AB 1020), >400.
"""
from __future__ import annotations

from app.models import FplRange, IncomeBracket

GUIDELINE_YEAR = 2026
BASE_100_PCT = 15_960       # household of 1, 100% of poverty, annual
PER_ADDITIONAL_PERSON = 5_680

# (upper bound in percent of poverty, tier). The last tier has no upper bound.
TIERS: list[tuple[int | None, FplRange]] = [
    (138, FplRange.UNDER_138),
    (200, FplRange.FROM_138_TO_200),
    (400, FplRange.FROM_200_TO_400),
    (None, FplRange.OVER_400),
]


def poverty_line(household_size: int) -> int:
    """100% of the 2026 poverty guideline for this household size, dollars per year."""
    if household_size < 1:
        raise ValueError("household_size must be at least 1")
    return BASE_100_PCT + PER_ADDITIONAL_PERSON * (household_size - 1)


def threshold(household_size: int, percent: int) -> int:
    """Annual income at `percent` of poverty, rounded to whole dollars (138% of 1 = 22,025)."""
    return round(poverty_line(household_size) * percent / 100)


def bracket_bounds(household_size: int) -> list[tuple[int | None, int | None, FplRange]]:
    """[(low, high, tier), ...] in dollars per year; low None for the first, high None for the last."""
    out, low = [], None
    for pct, tier in TIERS:
        high = threshold(household_size, pct) if pct is not None else None
        out.append((low, high, tier))
        low = high
    return out


def _money(n: int) -> str:
    return f"${n:,}"


def income_brackets(household_size: int) -> list[IncomeBracket]:
    """4 brackets in English labels, e.g. household of 1: 'Under $22,025 a year',
    '$22,025 to $31,920 a year', '$31,920 to $63,840 a year', 'Over $63,840 a year'.
    Onboarding renders its own en/es text from bracket_bounds()."""
    brackets = []
    for low, high, tier in bracket_bounds(household_size):
        if low is None:
            label = f"Under {_money(high)} a year"
        elif high is None:
            label = f"Over {_money(low)} a year"
        else:
            label = f"{_money(low)} to {_money(high)} a year"
        brackets.append(IncomeBracket(label=label, fpl_range=tier))
    return brackets
