"""Tests for #10 fpl: 2026 HHS poverty guidelines, 48 contiguous states (ASPE table)."""
import pytest

from app.conversation.fpl import bracket_bounds, income_brackets, poverty_line, threshold
from app.models import FplRange

# 100% column of the ASPE 2026 table for household sizes 1 to 6.
ASPE_100 = {1: 15_960, 2: 21_640, 3: 27_320, 4: 33_000, 5: 38_680, 6: 44_360}
# 138%, 200%, 400% columns of the same table (rounded to whole dollars).
ASPE = {
    1: (22_025, 31_920, 63_840),
    2: (29_863, 43_280, 86_560),
    3: (37_702, 54_640, 109_280),
    4: (45_540, 66_000, 132_000),
    5: (53_378, 77_360, 154_720),
    6: (61_217, 88_720, 177_440),
}


@pytest.mark.parametrize("n", range(1, 7))
def test_poverty_line_matches_aspe(n):
    assert poverty_line(n) == ASPE_100[n]


@pytest.mark.parametrize("n", range(1, 7))
def test_thresholds_match_aspe(n):
    assert (threshold(n, 138), threshold(n, 200), threshold(n, 400)) == ASPE[n]


@pytest.mark.parametrize("n", range(1, 7))
def test_brackets_cover_four_tiers_in_order(n):
    b = income_brackets(n)
    assert [x.fpl_range for x in b] == [FplRange.UNDER_138, FplRange.FROM_138_TO_200,
                                        FplRange.FROM_200_TO_400, FplRange.OVER_400]
    lo138, hi200, hi400 = ASPE[n]
    assert b[0].label == f"Under ${lo138:,} a year"
    assert b[1].label == f"${lo138:,} to ${hi200:,} a year"
    assert b[2].label == f"${hi200:,} to ${hi400:,} a year"
    assert b[3].label == f"Over ${hi400:,} a year"


def test_bounds_are_contiguous():
    bounds = bracket_bounds(3)
    assert bounds[0][0] is None and bounds[-1][1] is None
    for (_, high, _), (low, _, _) in zip(bounds, bounds[1:]):
        assert high == low


def test_household_must_be_positive():
    with pytest.raises(ValueError):
        poverty_line(0)
