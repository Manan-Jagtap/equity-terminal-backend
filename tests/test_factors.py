"""Unit tests for the multi-factor Alpha Score engine (pure ranking math)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")

from app.factors import _pct_ranks, trailing_return, realized_vol, score_universe


def test_pct_ranks_direction():
    hi = _pct_ranks([("a", 1), ("b", 2), ("c", 3)])
    assert hi["a"] == 0.0 and hi["c"] == 100.0
    lo = _pct_ranks([("a", 1), ("b", 2), ("c", 3)], higher_is_better=False)
    assert lo["a"] == 100.0 and lo["c"] == 0.0


def test_pct_ranks_skips_none_and_singleton():
    r = _pct_ranks([("a", None), ("b", 5), ("c", 10)])
    assert "a" not in r and r["b"] == 0.0 and r["c"] == 100.0
    assert _pct_ranks([("only", 7)]) == {"only": 50.0}


def test_trailing_return_flat_and_insufficient():
    assert abs(trailing_return([100.0] * 200)) < 1e-9
    assert trailing_return([100.0] * 50) is None       # not enough history


def test_realized_vol_flat_is_zero():
    assert realized_vol([100.0] * 130) == 0.0


def test_score_universe_ranks_dominant_name_first():
    rising = [100 + i for i in range(200)]
    falling = [300 - i * 0.3 for i in range(200)]
    rows = [
        {"ticker": "A", "mos": 0.30, "roe": 0.25, "pe": 15, "pb": 3, "closes": rising,  "growth": 0.20},
        {"ticker": "B", "mos": -0.20, "roe": 0.08, "pe": 40, "pb": 8, "closes": falling, "growth": 0.02},
    ]
    out = score_universe(rows)
    assert out[0]["ticker"] == "A" and out[0]["rank"] == 1
    assert out[0]["alpha_score"] >= out[1]["alpha_score"]
    assert set(out[0]["factors"]) == {"value", "quality", "momentum", "low_vol", "growth"}


def test_score_universe_handles_missing_factors():
    # No price series and no growth → value+quality still score; alpha not None.
    rows = [{"ticker": "X", "mos": 0.1, "roe": 0.2, "pe": 20, "pb": 4, "closes": [], "growth": None},
            {"ticker": "Y", "mos": 0.2, "roe": 0.1, "pe": 10, "pb": 2, "closes": [], "growth": None}]
    out = score_universe(rows)
    assert all(r["alpha_score"] is not None for r in out)
    assert all(r["factors"]["momentum"] is None for r in out)   # no price history
