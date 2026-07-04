"""
tests/test_cross_check.py — locks the second-source price cross-check logic.
Run: python tests/test_cross_check.py
"""
import os, sys
import datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.cross_check import check_row, DIVERGE_WARN

TODAY = dt.date(2026, 7, 3)          # a Friday


def row(**kw):
    base = {"ticker": "TEST",
            "snapshot_price": 100.0, "snapshot_date": TODAY,
            "hist_close": 100.0, "hist_date": TODAY - dt.timedelta(days=1)}
    base.update(kw)
    return base


def codes(r):
    return {f["code"] for f in r["flags"]}


def test_fresh_and_agreeing_is_ok():
    r = check_row(row(), TODAY)
    assert r["status"] == "ok" and not r["flags"]
    assert abs(r["gap_pct"]) < 1e-9
    print("  ok fresh+agreeing")


def test_small_gap_within_tolerance():
    r = check_row(row(snapshot_price=104.0), TODAY)   # +4% intraday move is normal
    assert r["status"] == "ok"
    print("  ok small gap tolerated")


def test_divergence_warns():
    r = check_row(row(snapshot_price=110.0), TODAY)   # +10%
    assert r["status"] == "warn" and "DIVERGENT" in codes(r)
    print("  ok divergence warns")


def test_split_shaped_divergence_alerts():
    r = check_row(row(snapshot_price=50.0), TODAY)    # -50% = 1:1 bonus shape
    assert r["status"] == "alert" and "DIVERGENT" in codes(r)
    print("  ok split-shaped alerts")


def test_stale_history_warns_and_skips_divergence():
    r = check_row(row(hist_date=TODAY - dt.timedelta(days=20),
                      snapshot_price=150.0), TODAY)
    assert "STALE_HISTORY" in codes(r)
    assert "DIVERGENT" not in codes(r)      # no vendor compare on stale data
    assert r["gap_pct"] is None
    print("  ok stale history")


def test_stale_snapshot_alerts():
    r = check_row(row(snapshot_date=TODAY - dt.timedelta(days=9)), TODAY)
    assert r["status"] == "alert" and "STALE_SNAPSHOT" in codes(r)
    print("  ok stale snapshot")


def test_missing_sources_flagged():
    r = check_row(row(hist_close=None, hist_date=None), TODAY)
    assert "NO_HISTORY" in codes(r)
    r2 = check_row(row(snapshot_price=None, snapshot_date=None), TODAY)
    assert r2["status"] == "alert" and "NO_SNAPSHOT" in codes(r2)
    print("  ok missing sources")


def test_weekend_gap_not_stale():
    # Monday check against Friday's close/snapshot must stay quiet.
    monday = dt.date(2026, 7, 6)
    friday = dt.date(2026, 7, 3)
    r = check_row(row(hist_date=friday, snapshot_date=friday), monday)
    assert r["status"] == "ok"
    print("  ok weekend gap quiet")


if __name__ == "__main__":
    test_fresh_and_agreeing_is_ok()
    test_small_gap_within_tolerance()
    test_divergence_warns()
    test_split_shaped_divergence_alerts()
    test_stale_history_warns_and_skips_divergence()
    test_stale_snapshot_alerts()
    test_missing_sources_flagged()
    test_weekend_gap_not_stale()
    print("test_cross_check: all passed")
