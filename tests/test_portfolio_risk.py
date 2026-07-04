"""
tests/test_portfolio_risk.py — locks the VaR / drawdown / XIRR math.
Run: python tests/test_portfolio_risk.py
"""
import os, sys
import datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.portfolio_risk import (portfolio_value_series, daily_returns, hist_var,
                                max_drawdown, xirr, risk_summary)


def dates(n, start="2025-01-01"):
    d0 = dt.date.fromisoformat(start)
    return [(d0 + dt.timedelta(days=i)).isoformat() for i in range(n)]


def test_series_strict_intersection_and_coverage():
    ds = dates(5)
    closes = {"A": {d: 10.0 for d in ds},
              "B": {d: 20.0 for d in ds[1:]},       # missing day 1
              "C": {}}                              # no history at all
    holdings = [{"ticker": "A", "qty": 2}, {"ticker": "B", "qty": 1}, {"ticker": "C", "qty": 5}]
    series, covered, uncovered = portfolio_value_series(holdings, closes)
    assert covered == ["A", "B"] and uncovered == ["C"]
    assert len(series) == 4                          # intersection drops day 1
    assert all(abs(v - 40.0) < 1e-9 for _, v in series)   # 2×10 + 1×20
    print("  ok series intersection")


def test_var_needs_sample_then_finds_tail():
    assert hist_var([0.01] * 30) is None             # too thin → no fabricated stat
    rets = [0.001] * 95 + [-0.05] * 5                # 5% of days lose 5%
    v = hist_var(rets, confidence=0.95)
    assert v is not None and 0.04 <= v <= 0.05
    print("  ok var")


def test_max_drawdown_peak_to_trough():
    ds = dates(6)
    series = list(zip(ds, [100, 120, 90, 60, 80, 110]))
    dd = max_drawdown(series)
    assert abs(dd["mdd"] - 0.5) < 1e-9               # 120 → 60
    assert dd["peak_date"] == ds[1] and dd["trough_date"] == ds[3]
    print("  ok drawdown")


def test_xirr_known_value():
    # ₹1000 in, ₹1100 out exactly one year later → 10%
    r = xirr([(dt.date(2025, 1, 1), -1000.0), (dt.date(2026, 1, 1), 1100.0)])
    assert r is not None and abs(r - 0.10) < 1e-3
    print("  ok xirr known value")


def test_xirr_degenerate_cases():
    assert xirr([(dt.date(2025, 1, 1), -1000.0)]) is None                 # one flow
    assert xirr([(dt.date(2025, 1, 1), -1.0), (dt.date(2025, 1, 2), -1.0)]) is None  # one sign
    # same-week round trip → suppressed (annualizing noise)
    assert xirr([(dt.date(2025, 1, 1), -1000.0), (dt.date(2025, 1, 3), 1001.0)]) is None
    print("  ok xirr degenerate")


def test_risk_summary_degrades_to_none():
    out = risk_summary([{"ticker": "A", "qty": 1}], {"A": {}}, [], 0.0)
    assert out["var_95_1d"] is None and out["max_drawdown"] is None
    assert out["xirr"] is None and out["uncovered"] == ["A"]
    print("  ok summary degrades")


if __name__ == "__main__":
    test_series_strict_intersection_and_coverage()
    test_var_needs_sample_then_finds_tail()
    test_max_drawdown_peak_to_trough()
    test_xirr_known_value()
    test_xirr_degenerate_cases()
    test_risk_summary_degrades_to_none()
    print("test_portfolio_risk: all passed")
