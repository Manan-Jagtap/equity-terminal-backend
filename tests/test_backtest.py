"""Unit tests for the track-record call ledger (pure functions, no DB).

NOTE: app.backtest imports app.models → app.database, which binds the engine
to DATABASE_URL at import time. Since pytest imports test files alphabetically
this file runs FIRST, so it must set the same test DB the other suites use —
otherwise the engine binds to the real local DB and their fixtures explode."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")

from datetime import date, timedelta

from app.backtest import compress_calls, aggregate


def _snap(d, verdict, price, ticker="TST"):
    return {"ticker": ticker, "date": d, "price": price, "verdict": verdict,
            "mos": 0.1, "intrinsic": price * 1.1, "valuation_sector": "IT_SERVICES"}


def test_single_open_call_marks_to_latest_price():
    snaps = [_snap("2026-06-01", "BUY", 100.0), _snap("2026-06-02", "BUY", 102.0),
             _snap("2026-06-03", "BUY", 101.0)]
    calls = compress_calls(snaps, latest_price=110.0)
    assert len(calls) == 1
    c = calls[0]
    assert c["open"] and c["verdict"] == "BUY"
    assert c["start_date"] == "2026-06-01" and c["start_price"] == 100.0
    assert abs(c["ret"] - 0.10) < 1e-12          # marked to latest, not last snap
    assert c["end_date"] is None


def test_verdict_change_closes_call_at_change_price():
    snaps = [_snap("2026-06-01", "BUY", 100.0), _snap("2026-06-05", "BUY", 108.0),
             _snap("2026-06-10", "HOLD", 120.0), _snap("2026-06-11", "HOLD", 121.0)]
    calls = compress_calls(snaps, latest_price=125.0)
    assert len(calls) == 2
    closed, opened = calls
    assert not closed["open"] and closed["verdict"] == "BUY"
    assert closed["end_date"] == "2026-06-10" and closed["end_price"] == 120.0
    assert abs(closed["ret"] - 0.20) < 1e-12
    assert closed["days"] == 9
    assert opened["open"] and opened["verdict"] == "HOLD"
    assert opened["start_date"] == "2026-06-10" and abs(opened["ret"] - (125.0 / 120.0 - 1)) < 1e-12


def test_daily_sampling_does_not_oversample():
    # 30 consecutive BUY days must still be ONE call.
    d0 = date(2026, 5, 1)
    snaps = [_snap((d0 + timedelta(days=i)).isoformat(), "BUY", 100.0 + i) for i in range(30)]
    assert len(compress_calls(snaps, 140.0)) == 1


def test_aggregate_directional_win_rates_and_spread():
    calls = [
        # BUY: +20% and -5% → avg 7.5%, win rate 0.5
        {"verdict": "BUY", "ret": 0.20, "days": 10, "open": True},
        {"verdict": "BUY", "ret": -0.05, "days": 10, "open": False},
        # AVOID: stock fell 10% → the model was RIGHT → win
        {"verdict": "AVOID", "ret": -0.10, "days": 10, "open": True},
        # HOLD: no direction → no win rate
        {"verdict": "HOLD", "ret": 0.02, "days": 10, "open": True},
    ]
    agg = aggregate(calls)
    c = agg["cohorts"]
    assert abs(c["BUY"]["avg_return"] - 0.075) < 1e-12
    assert c["BUY"]["win_rate"] == 0.5
    assert c["AVOID"]["win_rate"] == 1.0          # falling stock = correct AVOID
    assert c["HOLD"]["win_rate"] is None
    assert abs(agg["buy_avoid_spread"] - 0.175) < 1e-12   # 7.5% − (−10%)
    assert c["REDUCE"]["n"] == 0 and c["REDUCE"]["avg_return"] is None


def test_empty_ledger_is_valid():
    agg = aggregate([])
    assert agg["buy_avoid_spread"] is None
    assert all(v["n"] == 0 for v in agg["cohorts"].values())
