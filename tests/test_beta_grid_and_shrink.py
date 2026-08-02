"""Beta: shared sampling grid, and a fit floor before the prior is overridden.

Both defects found 2026-08-02 while diagnosing valuation dispersion.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import beta as B


def _mk(dates, start=100.0, step=1.01):
    out, lvl = {}, start
    for d in dates:
        out[d] = lvl; lvl *= step
    return out


def _dates(n, skip=()):
    return [f"2026-{1+(i//28)%12:02d}-{1+i%28:02d}" for i in range(n) if i not in skip]


def test_shared_grid_yields_overlapping_points():
    """The market grid must be reusable for a stock that missed some sessions."""
    mkt_dates = _dates(300)
    mkt = _mk(mkt_dates)
    stock = _mk([d for i, d in enumerate(mkt_dates) if i % 37])   # a few sessions missing
    mkt_wk = B._weekly_returns(mkt, mkt_dates)
    stock_wk = B._weekly_returns(stock, mkt_dates)                # SAME grid
    common = [d for d in mkt_wk if d in stock_wk]
    assert len(common) >= B.MIN_POINTS, f"only {len(common)} common points"


def test_own_grid_is_what_broke_it():
    """Negative control: sampling the stock on its OWN filtered dates collapses
    the overlap — the defect that left 973 of 997 names on the sector prior."""
    mkt_dates = _dates(300)
    mkt = _mk(mkt_dates)
    stock_dates = [d for i, d in enumerate(mkt_dates) if i % 37]
    stock = _mk(stock_dates)
    mkt_wk = B._weekly_returns(mkt, mkt_dates)
    bad = B._weekly_returns(stock, [d for d in mkt_dates if d in stock])  # own grid
    good = B._weekly_returns(stock, mkt_dates)                            # shared grid
    n_bad = len([d for d in mkt_wk if d in bad])
    n_good = len([d for d in mkt_wk if d in good])
    assert n_good > n_bad, "the shared grid must recover overlap the own-grid path loses"


def test_low_fit_does_not_override_the_prior():
    """R2 below the floor must return the prior EXACTLY — no partial credit."""
    for r2 in (0.0, 0.10, 0.215, B.R2_FLOOR - 1e-9):
        assert B.shrink(1.60, r2, 1.00) == 1.0
        assert B.shrink(0.30, r2, 1.20) == 1.2


def test_good_fit_still_moves_beta():
    """Above the floor the regression must still count, else the feature is dead."""
    assert B.shrink(1.60, 0.45, 1.00) > 1.20
    assert B.shrink(0.50, 0.50, 1.00) < 0.85


def test_shrink_respects_the_clamp():
    assert B.BETA_FLOOR <= B.shrink(9.0, 0.9, 1.0) <= B.BETA_CEIL
    assert B.BETA_FLOOR <= B.shrink(-4.0, 0.9, 1.0) <= B.BETA_CEIL
    assert B.BETA_FLOOR <= B.shrink(1.0, 0.01, 5.0) <= B.BETA_CEIL   # prior clamped too
