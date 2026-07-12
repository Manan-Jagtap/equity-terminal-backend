"""
test_safety.py — locks the defensive guards in the valuation engine so a bad
data row can never crash recommend() or fabricate a confident verdict.

Pure-compute: imports only engines / derive / data_quality (no DB), so it runs
anywhere. Run:  pytest tests/test_safety.py   ·   python tests/test_safety.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import engines
from app.derive import derive_assumptions
from app.data_quality import data_quality


def _a_nonfin():
    return derive_assumptions({}, "IT_SERVICES", False)


def _a_fin():
    return derive_assumptions({}, "BANK", True)


def _co(**over):
    co = dict(type="non-financial", ticker="TEST", name="Test", sector="IT",
              price=100.0, shares=100.0, equity=1000.0, net_profit=120.0,
              revenue=2000.0, net_debt=-200.0,
              series=[{"i": i, "close": 100.0 + i} for i in range(60)],
              synthetic_series=False, synthetic_price=False, nbfc=None)
    co.update(over)
    return co


def test_zero_price_no_crash():
    # B1: mos is None when price is 0 — verdict bands must not do None > 0.15.
    r = engines.recommend(_co(price=0.0), _a_nonfin())
    assert r["verdict"] in ("NO DATA", "LOW CONF")


def test_none_price_no_crash():
    r = engines.recommend(_co(price=None), _a_nonfin())
    assert r["verdict"] in ("NO DATA", "LOW CONF")


def test_nbfc_none_no_crash():
    # B2: co["nbfc"] explicitly None must not crash the financial branch.
    co = _co(type="financial", nbfc=None, revenue=None, net_debt=None)
    r = engines.recommend(co, _a_fin())
    assert "verdict" in r


def test_empty_series_no_crash():
    # B3: empty / missing price series must not crash technicals/_rsi/max.
    assert engines.recommend(_co(series=[]), _a_nonfin())["verdict"]
    assert engines.recommend(_co(series=None), _a_nonfin())["verdict"]
    # very short series (< RSI seed window) too
    short = [{"i": i, "close": 100.0 + i} for i in range(5)]
    assert engines.recommend(_co(series=short), _a_nonfin())["verdict"]


def test_synthetic_series_momentum_neutral():
    # C8: synthetic series must not fabricate bullish momentum.
    r = engines.recommend(_co(synthetic_series=True), _a_nonfin())
    mom = next(x for x in r["reasons"] if x["label"] == "Momentum")
    assert mom["score"] == 50 and not mom["good"]


def test_synthetic_price_forces_low_conf():
    # C9: a missing live price must never produce a BUY off a bogus margin of
    # safety. The engine now goes further than LOW CONF: a synthetic sentinel
    # price yields mos=None → verdict NO DATA (the honest state), and the
    # confidence score still drops below the reliable threshold.
    co = _co(synthetic_price=True)
    assert data_quality(co)["score"] < 0.5
    rec = engines.recommend(co, _a_nonfin())
    assert rec["verdict"] == "NO DATA"
    assert rec["mos"] is None


def test_normal_company_sane():
    r = engines.recommend(_co(), _a_nonfin())
    assert r["intrinsic"] is not None and r["intrinsic"] > 0
    assert r["mos"] is not None
    assert r["verdict"] in ("BUY", "ACCUMULATE", "HOLD", "REDUCE", "AVOID", "LOW CONF")


def test_negative_equity_financial_low_conf():
    # Negative net worth → book-based valuation meaningless → not reliable.
    co = _co(type="financial", equity=-500.0, nbfc={}, revenue=None, net_debt=None)
    dq = data_quality(co)
    assert dq["score"] < 0.5


def _stmts(rev0, margin, nw, borrow, n=5, g=0.08):
    s = {}
    for k in range(n):
        yr = 2020 + k
        rev = rev0 * (1 + g) ** k
        ebit = rev * margin
        s[yr] = {"PL": {"revenue": rev, "ebit": ebit, "ebitda": ebit * 1.15,
                        "pat": ebit * 0.74, "tax": ebit * 0.26, "pbt": ebit},
                 "BS": {"net_worth": nw * (1 + g) ** k, "borrowings": borrow}, "CF": {}}
    return s


def test_company_roic_lifts_high_roic_only():
    # Capital-light, no debt → very high realised ROIC → LOWER reinvestment
    # (more free cash) than the flat sector rate.
    hi = derive_assumptions(_stmts(23000, 0.22, 5157, 0), "CONSUMER", False)
    # Heavy capital base, thin margin → ROIC below sector → reinvestment must
    # NOT be lifted (clamp floors at sector, so it equals the sector rate).
    mid = derive_assumptions(_stmts(50000, 0.15, 60000, 40000), "MANUFACTURING", False)
    import app.sector_params as SP
    sector_consumer = min(max(hi["rev_growth"] / SP.params("CONSUMER")["mature_roic"], 0.10), 0.80)
    sector_manu = min(max(mid["rev_growth"] / SP.params("MANUFACTURING")["mature_roic"], 0.10), 0.80)
    assert hi["reinvest_rate"] < sector_consumer - 0.05   # genuinely lifted
    assert abs(mid["reinvest_rate"] - sector_manu) < 1e-3  # unchanged for sub-sector ROIC (4dp rounding)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ✓ {fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ✗ {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(fns)} passed.")
    sys.exit(0 if passed == len(fns) else 1)


def test_implausible_mos_forces_low_conf():
    # The AWL/REDINGTON class of failure: internally-consistent inputs, but the
    # sector model produces an intrinsic many times the price (thin-margin
    # business on premium multiples). Must read LOW CONF, never a confident BUY.
    co = _co(price=10.0)                     # tiny price vs healthy fundamentals
    r = engines.recommend(co, _a_nonfin())
    assert r["mos"] is not None and r["mos"] > 2.0   # the setup must reach the guard
    assert r["verdict"] == "LOW CONF"
    assert r["reliable"] is False
    assert any("Implausible margin of safety" in (x.get("note") or "")
               for x in r["reasons"])


def test_plausible_mos_untouched():
    # A normal-gap name must NOT trip the implausibility guard.
    r = engines.recommend(_co(), _a_nonfin())
    assert r["mos"] is None or r["mos"] <= 2.0 or r["verdict"] == "LOW CONF"
    if r["mos"] is not None and 0 < r["mos"] <= 2.0:
        assert r["verdict"] != "LOW CONF" or r["confidence"]["score"] < 0.5


def test_cagr_sign_flip_returns_none_not_complex():
    # (negative/positive) ** (1/3) is a COMPLEX number in Python — it crashed
    # /financials with a 500 for names transitioning profit→loss.
    from app.metrics import _cagr
    assert _cagr(100.0, -50.0, 3) is None
    assert _cagr(-100.0, 50.0, 3) is None
    r = _cagr(100.0, 150.0, 3)
    assert isinstance(r, float) and r > 0
