"""
tests/test_cross_check.py — locks the second-source price cross-check logic.
Run: python tests/test_cross_check.py
"""
import os, sys
import datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.cross_check import check_row, DIVERGE_WARN, _dhan_coverage_lookup

TODAY = dt.date(2026, 7, 3)          # a Friday


# The divergence compare only fires on SAME-TRADING-DAY prices: intraday the
# snapshot leads the Dhan EOD history by a day, and comparing across days just
# measures the day's move (a false-positive class the checker deliberately
# removed). Divergence rows therefore use hist_date == snapshot_date; the
# different-day quietness is locked by its own test below.
def row(**kw):
    base = {"ticker": "TEST",
            "snapshot_price": 100.0, "snapshot_date": TODAY,
            "hist_close": 100.0, "hist_date": TODAY}
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


def test_different_day_prices_never_compared():
    # Intraday state: snapshot is today, Dhan history still holds yesterday's
    # close. A big gap here is just today's move — must NOT flag DIVERGENT.
    r = check_row(row(hist_date=TODAY - dt.timedelta(days=1),
                      snapshot_price=110.0), TODAY)
    assert "DIVERGENT" not in codes(r)
    assert r["gap_pct"] is None
    print("  ok cross-day compare suppressed")


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


# --- Dhan coverage (added 2026-08-01) ---------------------------------------
# JBCHEPHARM survived a credential rotation, a full backfill and an entitlement
# check still reporting "Dhan history last updated 12d ago" — wording that sends
# you after the pipeline when the name is simply absent from the scrip master.

def test_uncovered_name_reports_coverage_not_staleness():
    import datetime as dt
    r = check_row({"ticker": "JBCHEPHARM", "snapshot_price": 2408.9,
                      "snapshot_date": dt.date(2026, 7, 29),
                      "hist_close": 2350.0, "hist_date": dt.date(2026, 7, 15),
                      "dhan_covered": False}, today=dt.date(2026, 8, 1))
    codes = {f["code"] for f in r["flags"]}
    assert "NO_DHAN_COVERAGE" in codes
    assert "STALE_HISTORY" not in codes, "an absent instrument must not read as a broken pipeline"


def test_covered_name_still_reports_stale_history():
    import datetime as dt
    r = check_row({"ticker": "FOO", "snapshot_price": 100.0,
                      "snapshot_date": dt.date(2026, 8, 1),
                      "hist_close": 98.0, "hist_date": dt.date(2026, 7, 15),
                      "dhan_covered": True}, today=dt.date(2026, 8, 1))
    codes = {f["code"] for f in r["flags"]}
    assert "STALE_HISTORY" in codes
    assert "NO_DHAN_COVERAGE" not in codes


def test_unknown_coverage_falls_back_to_old_behaviour():
    """Scrip master unavailable -> must NOT relabel the universe as uncovered."""
    import datetime as dt
    for missing in ({}, {"dhan_covered": None}):
        row = {"ticker": "FOO", "snapshot_price": 100.0,
               "snapshot_date": dt.date(2026, 8, 1),
               "hist_close": 98.0, "hist_date": dt.date(2026, 7, 15), **missing}
        r = check_row(row, today=dt.date(2026, 8, 1))
        codes = {f["code"] for f in r["flags"]}
        assert "STALE_HISTORY" in codes
        assert "NO_DHAN_COVERAGE" not in codes


def test_coverage_lookup_returns_none_when_master_empty(monkeypatch):
    """An empty map must surface as None, never as 'nothing is covered'."""
    from app.dhan import instruments
    monkeypatch.setattr(instruments, "_load", lambda *a, **k: None)
    monkeypatch.setitem(instruments._cache, "eq", {})
    assert _dhan_coverage_lookup() is None
