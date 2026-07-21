"""Batch 15 — FIX-23: track-record honesty tail (ENG-04/05/06).
  · Verdict backtest no longer freezes delisted open calls at 0% (marks to last
    known price, flags stale).
  · calibrate() ignores HOLD (no directional claim).
  · Factor track annualises returns for horizon-uniformity and keeps price-less
    names at their last-known mark."""
from app.backtest import compress_calls, calibrate
from app.factors import alpha_backtest


# ── ENG-05: open call marked to last-known price, not frozen at entry ──────────

def test_open_call_marks_to_last_known_when_delisted():
    snaps = [
        {"ticker": "DEL", "date": "2026-01-01", "price": 100.0, "verdict": "BUY", "mos": 0.2},
        {"ticker": "DEL", "date": "2026-03-01", "price": 60.0, "verdict": "BUY", "mos": 0.2},
    ]
    # latest_price=None (dropped from MarketSnapshot) → mark to the last snapshot (60), not entry (100)
    calls = compress_calls(snaps, latest_price=None)
    assert len(calls) == 1
    c = calls[0]
    assert c["open"] and c["stale_price"] is True
    assert c["end_price"] == 60.0
    assert abs(c["ret"] - (-0.40)) < 1e-9        # −40%, NOT a frozen 0%


def test_open_call_live_price_is_not_flagged_stale():
    snaps = [{"ticker": "OK", "date": "2026-01-01", "price": 100.0, "verdict": "BUY", "mos": 0.1}]
    c = compress_calls(snaps, latest_price=130.0)[0]
    assert c["open"] and "stale_price" not in c
    assert abs(c["ret"] - 0.30) < 1e-9


# ── ENG-06: HOLD contributes no wins / hit_rate to calibrate() ────────────────

def test_calibrate_ignores_hold():
    scored = [
        {"verdict": "HOLD", "mos": 0.0, "ret": -0.20},   # would be a "win" if scored bearish
        {"verdict": "HOLD", "mos": 0.0, "ret": -0.30},
        {"verdict": "BUY",  "mos": 0.2, "ret": 0.15},
    ]
    cal = calibrate(scored, snapshot_days=200)
    keys = cal["buckets"].keys()
    assert not any(k.startswith("HOLD") for k in keys)   # HOLD bucket never created
    assert any(k.startswith("BUY") for k in keys)


# ── ENG-04: annualisation makes horizons comparable; short windows dropped ────

def test_annualisation_makes_horizons_comparable():
    # two names, SAME raw +10% move, but over very different windows → after
    # annualising, the short-window name shows a far larger per-year figure.
    slow = alpha_backtest([{"ticker": "S", "alpha0": 50, "price0": 100, "price_now": 110,
                            "days": 365}] * 1 + [{"ticker": "S2", "alpha0": 40, "price0": 100,
                            "price_now": 110, "days": 365}], buckets=2)
    fast_rows = [{"ticker": "F", "alpha0": 50, "price0": 100, "price_now": 110, "days": 60},
                 {"ticker": "F2", "alpha0": 40, "price0": 100, "price_now": 110, "days": 60}]
    fast = alpha_backtest(fast_rows, buckets=2)
    assert fast["buckets"][0]["avg_return"] > slow["buckets"][0]["avg_return"]


def test_short_windows_dropped():
    rows = [{"ticker": f"T{i}", "alpha0": 90 - i, "price0": 100, "price_now": 105, "days": 10}
            for i in range(6)]
    bt = alpha_backtest(rows, buckets=5)     # all < 30 days → nothing usable
    assert bt["n"] == 0 and bt["buckets"] == []


def test_priceless_name_kept_at_last_mark():
    # price_now already carries the last-known mark (main.py fills it); the
    # backtest must count it, not drop it.
    rows = [{"ticker": f"T{i}", "alpha0": 90 - i * 8, "price0": 100.0,
             "price_now": 100 * (1 + (90 - i * 8) / 500), "days": 200} for i in range(10)]
    bt = alpha_backtest(rows, buckets=5)
    assert bt["n"] == 10          # none dropped
