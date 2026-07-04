"""Unit tests for the multi-factor Alpha Score engine (pure ranking math)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")

from app.factors import (_pct_ranks, trailing_return, realized_vol, score_universe,
                         portfolio_xray, sector_strength, alpha_backtest)


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
    assert set(out[0]["factors"]) == {"value", "quality", "momentum", "low_vol", "growth", "catalyst", "surprise"}


def test_score_universe_handles_missing_factors():
    # No price series and no growth → value+quality still score; alpha not None.
    rows = [{"ticker": "X", "mos": 0.1, "roe": 0.2, "pe": 20, "pb": 4, "closes": [], "growth": None},
            {"ticker": "Y", "mos": 0.2, "roe": 0.1, "pe": 10, "pb": 2, "closes": [], "growth": None}]
    out = score_universe(rows)
    assert all(r["alpha_score"] is not None for r in out)
    assert all(r["factors"]["momentum"] is None for r in out)   # no price history


def test_catalyst_factor_participates_when_present():
    # Isolate catalyst: all other factor inputs absent (None) so only the
    # revision signal drives the score (avoids arbitrary tie-splitting).
    rows = [
        {"ticker": "A", "mos": None, "roe": None, "pe": None, "pb": None, "closes": [], "growth": None, "catalyst": 0.30},
        {"ticker": "B", "mos": None, "roe": None, "pe": None, "pb": None, "closes": [], "growth": None, "catalyst": -0.10},
    ]
    m = {r["ticker"]: r for r in score_universe(rows)}
    assert m["A"]["factors"]["catalyst"] == 100.0 and m["B"]["factors"]["catalyst"] == 0.0
    assert m["A"]["alpha_score"] > m["B"]["alpha_score"]        # upgraded name ranks higher


def test_catalyst_none_degrades_gracefully():
    rows = [{"ticker": "X", "mos": 0.2, "roe": 0.2, "pe": 20, "pb": 4, "closes": [], "catalyst": None}]
    out = score_universe(rows)
    assert out[0]["factors"]["catalyst"] is None and out[0]["alpha_score"] is not None


def test_portfolio_xray_weights_sizing_and_aggregates():
    items = [
        {"ticker": "A", "sector": "IT",   "value": 6000, "alpha_score": 80,
         "factors": {"value": 70, "quality": 80, "momentum": 60, "low_vol": 50, "growth": 40, "catalyst": None}, "volatility": 0.20},
        {"ticker": "B", "sector": "IT",   "value": 3000, "alpha_score": 30,
         "factors": {"value": 40, "quality": 30, "momentum": 50, "low_vol": 40, "growth": 20, "catalyst": None}, "volatility": 0.40},
        {"ticker": "C", "sector": "Bank", "value": 1000, "alpha_score": 55,
         "factors": {"value": 60, "quality": 50, "momentum": 45, "low_vol": 55, "growth": 30, "catalyst": None}, "volatility": 0.25},
    ]
    x = portfolio_xray(items)
    assert abs(items[0]["weight"] - 0.6) < 1e-9                 # actual weight
    # inverse-vol: low-vol A gets more than high-vol B; suggested weights sum to 1
    assert items[0]["suggested_weight"] > items[1]["suggested_weight"]
    assert abs(sum(i["suggested_weight"] for i in items) - 1.0) < 1e-9
    assert any("Over" in f for f in items[0]["flags"])          # A is 60% of book
    assert "Low Alpha" in items[1]["flags"]                     # B alpha 30 < 40
    assert x["n"] == 3 and x["weighted_alpha"] is not None
    top = x["sector_concentration"][0]
    assert top["sector"] == "IT" and abs(top["weight"] - 0.9) < 1e-9
    assert x["hhi"] is not None


def test_portfolio_xray_empty():
    x = portfolio_xray([])
    assert x["n"] == 0 and x["total_value"] == 0 and x["weighted_alpha"] is None


def test_sector_strength_aggregates_and_sorts():
    ranked = [
        {"ticker": "A", "sector": "IT", "alpha_score": 80, "factors": {"quality": 90}},
        {"ticker": "B", "sector": "IT", "alpha_score": 60, "factors": {"quality": 80}},
        {"ticker": "C", "sector": "Bank", "alpha_score": 40, "factors": {"quality": 30}},
    ]
    out = sector_strength(ranked)
    assert [s["sector"] for s in out] == ["IT", "Bank"]        # sorted by avg_alpha desc
    assert out[0]["n"] == 2 and out[0]["avg_alpha"] == 70.0 and out[0]["factors"]["quality"] == 85.0


def test_alpha_backtest_buckets_and_spread():
    # 10 names, alpha 100..10, flat entry price, exit priced so higher alpha → higher return
    rows = [{"ticker": f"T{i}", "alpha0": 100 - i * 10, "price0": 100.0,
             "price_now": 100 * (1 + (100 - i * 10) / 1000)} for i in range(10)]
    bt = alpha_backtest(rows, buckets=5)
    assert bt["n"] == 10 and len(bt["buckets"]) == 5
    assert bt["top_minus_bottom"] > 0                          # Q1 (high Alpha) beats Q5
    assert bt["buckets"][0]["avg_alpha"] > bt["buckets"][-1]["avg_alpha"]


def test_alpha_backtest_insufficient_history():
    bt = alpha_backtest([{"ticker": "A", "alpha0": 50, "price0": 100, "price_now": 110}], buckets=5)
    assert bt["n"] == 1 and bt["buckets"] == [] and bt["top_minus_bottom"] is None


def test_surprise_factor_participates_and_renormalizes():
    # Isolate the surprise factor (all other inputs None, like the catalyst-only
    # test): alpha is then driven purely by the beat/miss rank.
    base = {"mos": None, "roe": None, "pe": None, "pb": None, "closes": [], "growth": None}
    rows = [{"ticker": "BEAT", **base, "surprise": 8.0},
            {"ticker": "MISS", **base, "surprise": -6.0}]
    out = {r["ticker"]: r for r in score_universe(rows)}
    assert out["BEAT"]["factors"]["surprise"] == 100.0
    assert out["MISS"]["factors"]["surprise"] == 0.0
    assert out["BEAT"]["alpha_score"] > out["MISS"]["alpha_score"]
    # Without surprise data the factor is simply None (score renormalizes
    # over whatever else exists — here nothing, so alpha is None too).
    out2 = score_universe([{"ticker": "A", **base}])
    assert out2[0]["factors"]["surprise"] is None
    assert out2[0]["alpha_score"] is None
