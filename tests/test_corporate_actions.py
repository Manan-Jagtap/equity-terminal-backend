"""Unit tests for the corporate-action back-adjustment + total-return engine.

Pure functions, no DB — but keep the standard DATABASE_URL guard so collection
order can never bind the engine to a real DB via a sibling import."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")

import pytest

from app.corporate_actions import (
    price_factor, adjust_price_series, dividends_in_window, total_return,
)

# Realistic events (Bajaj Finance June-2025 style + a synthetic later bonus).
SPLIT = {"action_type": "split", "ex_date": "2025-06-16", "ratio": 0.5, "value": None}   # FV 2→1
BONUS = {"action_type": "bonus", "ex_date": "2025-09-01", "ratio": 0.2, "value": None}   # 4:1
DIV   = {"action_type": "dividend", "ex_date": "2025-03-01", "value": 40.0, "ratio": None}


# ── price_factor ────────────────────────────────────────────────────────────
def test_factor_no_actions_is_one():
    assert price_factor("2025-01-01", []) == 1.0

def test_dividends_do_not_move_price_factor():
    assert price_factor("2025-01-01", [DIV]) == 1.0

def test_split_back_adjusts_earlier_close():
    # a close BEFORE the split ex-date sits on the old (2x) basis → ×0.5
    assert price_factor("2025-06-15", [SPLIT]) == 0.5

def test_close_on_ex_date_not_adjusted():
    # on the ex-date the quote already reflects the split
    assert price_factor("2025-06-16", [SPLIT]) == 1.0

def test_close_after_split_not_adjusted():
    assert price_factor("2025-07-01", [SPLIT]) == 1.0

def test_events_compound_in_date_order():
    acts = [SPLIT, BONUS]
    assert price_factor("2025-01-01", acts) == pytest.approx(0.5 * 0.2)   # before both
    assert price_factor("2025-07-01", acts) == pytest.approx(0.2)         # after split only
    assert price_factor("2025-10-01", acts) == 1.0                        # after both


# ── adjust_price_series ─────────────────────────────────────────────────────
def test_series_unchanged_without_split_or_bonus():
    pts = [{"date": "2025-01-01", "close": 100.0}, {"date": "2025-02-01", "close": 110.0}]
    assert adjust_price_series(pts, [DIV]) == pts

def test_series_back_adjusts_ohlc_leaves_volume():
    pts = [
        {"date": "2025-06-15", "open": 12000, "high": 12100, "low": 11900, "close": 12000, "volume": 5},
        {"date": "2025-06-17", "open": 6100, "high": 6150, "low": 6050, "close": 6100, "volume": 9},
    ]
    out = adjust_price_series(pts, [SPLIT])
    assert out[0]["close"] == 6000 and out[0]["open"] == 6000 and out[0]["high"] == 6050
    assert out[0]["volume"] == 5                 # volume untouched
    assert out[1]["close"] == 6100               # post-split row unchanged
    assert pts[0]["close"] == 12000              # input not mutated

def test_series_none_fields_survive():
    pts = [{"date": "2025-01-01", "close": 100.0, "open": None}]
    out = adjust_price_series(pts, [SPLIT])
    assert out[0]["open"] is None and out[0]["close"] == 50.0


# ── dividends_in_window ─────────────────────────────────────────────────────
def test_dividend_window_is_exclusive_start_inclusive_end():
    d = {"action_type": "dividend", "ex_date": "2025-03-01", "value": 10.0, "ratio": None}
    assert dividends_in_window("2025-03-01", "2025-12-31", [d]) == 0.0   # == start excluded
    assert dividends_in_window("2025-01-01", "2025-03-01", [d]) == 10.0  # == end included

def test_dividend_scaled_by_later_split():
    # ₹40 paid before a 2:1 split → ₹20 per current-basis share
    assert dividends_in_window("2025-01-01", "2025-12-31", [DIV, SPLIT]) == pytest.approx(20.0)


# ── total_return ────────────────────────────────────────────────────────────
def test_total_return_matches_legacy_price_return_without_actions():
    r = total_return("2025-01-01", "2025-12-31", 100.0, 120.0, [])
    assert r["price"] == pytest.approx(0.2)
    assert r["dividend"] == 0.0
    assert r["total"] == pytest.approx(0.2)

def test_total_return_adds_dividend_yield():
    r = total_return("2025-01-01", "2025-12-31", 100.0, 120.0,
                     [{"action_type": "dividend", "ex_date": "2025-06-01", "value": 5.0, "ratio": None}])
    assert r["price"] == pytest.approx(0.2)
    assert r["dividend"] == pytest.approx(0.05)
    assert r["total"] == pytest.approx(0.25)

def test_total_return_handles_split_inside_window():
    # start 12000 pre 2:1 split, end 6100 today → +1.667% price, plus scaled div
    r = total_return("2025-01-01", "2025-12-31", 12000.0, 6100.0, [SPLIT, DIV])
    assert r["price"] == pytest.approx(6100 / 6000 - 1)
    assert r["dividend"] == pytest.approx(20.0 / 6000.0)     # ₹40→₹20 over adj start 6000
    assert r["total"] == pytest.approx(r["price"] + r["dividend"])

def test_total_return_none_on_bad_inputs():
    assert total_return("2025-01-01", "2025-12-31", 0, 120.0, []) is None
    assert total_return("2025-01-01", "2025-12-31", None, 120.0, []) is None
    assert total_return("2025-01-01", "2025-12-31", 100.0, None, []) is None
