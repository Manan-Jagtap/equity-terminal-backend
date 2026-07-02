"""Unit tests for portfolio totals math (pure function, no DB).

NOTE: app.portfolio_routes imports app.models → app.database, which binds the
engine to DATABASE_URL at import time — so this file must set the same test DB
the other suites use (see tests/test_backtest.py)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")

from datetime import datetime
from app.portfolio_routes import compute_totals, _dividend_income


def _item(value, cost, mos=None):
    return {"value": value, "cost": cost, "mos": mos,
            "pnl": None, "pnl_pct": None, "weight": None}


def test_totals_value_cost_pnl_and_weights():
    items = [_item(1500.0, 1000.0), _item(500.0, 600.0)]
    totals = compute_totals(items)
    assert totals["value"] == 2000.0
    assert totals["cost"] == 1600.0
    assert totals["pnl"] == 400.0
    assert abs(totals["pnl_pct"] - 0.25) < 1e-12
    # Per-item enrichment happens in the same pass.
    assert abs(items[0]["weight"] - 0.75) < 1e-12
    assert abs(items[1]["weight"] - 0.25) < 1e-12
    assert items[0]["pnl"] == 500.0
    assert abs(items[1]["pnl_pct"] - (-100.0 / 600.0)) < 1e-12


def test_weighted_mos_skips_null_mos_and_is_value_weighted():
    items = [_item(3000.0, 2000.0, mos=0.20),
             _item(1000.0, 1000.0, mos=-0.10),
             _item(2000.0, 1500.0, mos=None)]   # no valuation → excluded
    totals = compute_totals(items)
    # weighted over the 4000 of value that HAS a MoS: (3000*0.2 + 1000*-0.1)/4000
    assert abs(totals["weighted_mos"] - 0.125) < 1e-12
    # but portfolio weights span ALL value (6000)
    assert abs(items[2]["weight"] - (2000.0 / 6000.0)) < 1e-12


def test_unpriced_holding_and_empty_portfolio_are_safe():
    # A holding with no MarketSnapshot has value=None: excluded from value and
    # weights, but its cost still counts.
    items = [_item(None, 800.0), _item(1200.0, 1000.0, mos=0.05)]
    totals = compute_totals(items)
    assert totals["value"] == 1200.0
    assert totals["cost"] == 1800.0
    assert items[0]["weight"] is None and items[0]["pnl"] is None
    assert abs(totals["weighted_mos"] - 0.05) < 1e-12

    empty = compute_totals([])
    assert empty["value"] == 0 and empty["cost"] == 0 and empty["pnl"] == 0
    assert empty["pnl_pct"] is None and empty["weighted_mos"] is None
    assert empty["div_income"] == 0 and empty["total_pnl"] == 0


def test_total_pnl_includes_dividend_income():
    items = [{"value": 1200.0, "cost": 1000.0, "mos": None, "div_income": 50.0,
              "pnl": None, "pnl_pct": None, "weight": None}]
    totals = compute_totals(items)
    assert items[0]["pnl"] == 200.0                      # capital only
    assert items[0]["total_pnl"] == 250.0               # + ₹50 dividends
    assert abs(items[0]["total_pnl_pct"] - 0.25) < 1e-12
    assert totals["div_income"] == 50.0
    assert totals["total_pnl"] == 250.0
    assert abs(totals["total_pnl_pct"] - 0.25) < 1e-12


def test_backward_compat_items_without_div_income():
    # Legacy items (no div_income key) must still total correctly → total==capital.
    items = [{"value": 1500.0, "cost": 1000.0, "mos": None,
              "pnl": None, "pnl_pct": None, "weight": None}]
    totals = compute_totals(items)
    assert totals["div_income"] == 0.0 and totals["total_pnl"] == totals["pnl"] == 500.0


def test_dividend_income_since_added_scaled_by_split():
    actions = [{"action_type": "dividend", "ex_date": "2025-03-01", "value": 40.0, "ratio": None},
               {"action_type": "split", "ex_date": "2025-06-16", "value": None, "ratio": 0.5}]
    # ₹40 paid pre-split → ₹20 on current basis × 10 shares = ₹200
    assert abs(_dividend_income(10, datetime(2025, 1, 1), actions) - 200.0) < 1e-9
    # a dividend before the position was opened is excluded
    assert _dividend_income(10, datetime(2025, 4, 1), actions) == 0.0
