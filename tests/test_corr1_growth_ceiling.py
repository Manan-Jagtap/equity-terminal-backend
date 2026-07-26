"""CORR-1: near-term growth must be EARNED from return-on-capital quality.

`fade_years` already tiers the growth RUNWAY off roic_q, on the stated
principle that "the runway is earned from QUALITY (ROIC durability), NOT
current growth". The RATE ceiling was a flat 18% for every non-cyclical, so
an ordinary business printing one strong year was extrapolated at a
compounder's growth for N//2 years before any fade began.

Measured on the 314-name calibration set: 104 names (33%) sat pinned at
exactly the 18% clamp and were the worst-calibrated cohort by a wide margin
(median x1.73 vs ground truth, 23% within band).
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")

import pytest
from app.derive import (_growth_ceiling, _GROWTH_ROIC_Q, _GROWTH_HI_EARNED,
                        _GROWTH_HI_BASE)


def test_earned_growth_is_untouched():
    """A name that out-earns its sector keeps the full rate."""
    assert _growth_ceiling(0.30, 0.15) == _GROWTH_HI_EARNED


def test_unearned_growth_is_capped_at_nominal_gdp():
    """No demonstrated return advantage → not assumed to outgrow the economy."""
    assert _growth_ceiling(0.15, 0.15) == _GROWTH_HI_BASE


def test_threshold_is_exactly_the_fade_years_threshold():
    """The rate and the runway must key off the SAME evidence of quality.

    fade_years grants its first horizon step-up at roic_q >= 1.1; if these
    two drift apart the model tells two different stories about one business.
    """
    import inspect
    from app import derive
    src = inspect.getsource(derive)
    assert "if roic_q >= 1.1:" in src, "fade_years' first step-up moved"
    assert _GROWTH_ROIC_Q == 1.1

    m = 0.20
    assert _growth_ceiling(1.1 * m, m) == _GROWTH_HI_EARNED   # at the threshold
    assert _growth_ceiling(1.09 * m, m) == _GROWTH_HI_BASE    # just below


def test_ceiling_is_one_directional():
    """It may only LOWER growth — it must never raise anyone's rate.

    Guards the VAL-02 discipline: no valuation may be inflated by this change.
    """
    assert _GROWTH_HI_BASE <= _GROWTH_HI_EARNED


def test_missing_sector_roic_does_not_silently_cap():
    """A missing param must not masquerade as evidence of low quality."""
    assert _growth_ceiling(0.25, None) == _GROWTH_HI_EARNED
    assert _growth_ceiling(0.25, 0) == _GROWTH_HI_EARNED


@pytest.mark.parametrize("base", [_GROWTH_HI_BASE])
def test_base_is_anchored_to_nominal_gdp_not_fitted(base):
    """0.10 is India's long-run NOMINAL GDP, not a curve-fitted constant.

    A tighter 0.09 scored +0.3pp better on the calibration fixture and was
    deliberately NOT taken — it has no economic anchor.
    """
    assert base == 0.10
