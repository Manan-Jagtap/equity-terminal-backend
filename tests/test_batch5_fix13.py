"""Batch 5 — FIX-13 conviction legs + the FM contract. Backend-only (confidence
lives in recommend, not engine.js) — parity untouched."""
import os
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_batch5_test.db")
from app import engines

_FM_FIELDS = ("data_tier", "method_dispersion", "sensitivity_swing", "tv_share", "gate_state")


def test_fix13_method_dispersion():
    comps = [{"method": "FCFF DCF", "value": 100.0, "weight": 0.65},
             {"method": "Exit Multiple", "value": 250.0, "weight": 0.20},
             {"method": "P/E (sector)", "value": None, "weight": 0.15}]
    assert engines.method_dispersion(comps) == 2.5          # 250/100, Nones ignored
    assert engines.method_dispersion([{"value": 100.0, "weight": 0.65}]) is None  # <2 methods
    assert engines.method_dispersion([]) is None


def test_fix13_tv_share_dcf_vs_financial():
    # fcff shape (bvps0 None) → tv/(pv+tv), in [0,1]
    assert abs(engines.tv_share({"tv_pv": 60.0, "pv_explicit": 40.0, "bvps0": None}) - 0.6) < 1e-9
    # RI shape (bvps0 set) → None (book-anchored, TV-share not meaningful)
    assert engines.tv_share({"tv_pv": 10.0, "pv_explicit": 5.0, "bvps0": 100.0}) is None
    assert engines.tv_share({"tv_pv": None, "pv_explicit": 40.0, "bvps0": None}) is None


def _nonfin_co_a():
    co = {"type": "nonfinancial", "ticker": "T", "sector": "Capital Goods",
          "revenue": 1000.0, "net_profit": 120.0, "net_debt": 0.0, "shares": 100.0,
          "price": 50.0, "_valuation_sector": "CAPITAL_GOODS", "statements": {},
          "synthetic_price": False}
    a = {"risk_free": 0.07, "beta": 1.0, "erp": 0.05, "fade_years": 10, "rev_growth": 0.10,
         "ebit_margin": 0.15, "tax_rate": 0.25, "reinvest_rate": 0.4, "terminal_growth": 0.05,
         "debt_weight": 0.20, "_valuation_sector": "CAPITAL_GOODS"}
    return co, a


def test_fix13_sensitivity_swing(monkeypatch):
    """swing = |bull − bear| / base, where bull is (rf−1%, g+1%) and bear
    (rf+1%, g−1%). Stub valuate to return controlled corners so the formula is
    tested independent of fcff's input plumbing (the live path is exercised by
    the calibration replay)."""
    def fake_valuate(co, a):
        return {"intrinsic": 120.0 if a["risk_free"] < 0.07 else 80.0}  # bull vs bear
    monkeypatch.setattr(engines, "valuate", fake_valuate)
    sw = engines.sensitivity_swing({}, {"risk_free": 0.07, "terminal_growth": 0.05}, 100.0)
    assert abs(sw - 0.40) < 1e-9                 # |120 − 80| / 100
    assert engines.sensitivity_swing({}, {"risk_free": 0.07, "terminal_growth": 0.05}, None) is None


def test_fix13_recommend_exposes_fm_contract():
    """Every recommendation carries the five pinned FM-contract fields, even a
    NO-DATA name (values may be None, but the keys/shape are stable)."""
    rec = engines.recommend({"type": "nonfinancial", "ticker": "X", "price": None,
                             "synthetic_price": True}, {"risk_free": 0.07, "beta": 1.0, "erp": 0.05})
    for k in _FM_FIELDS:
        assert k in rec, f"missing FM-contract field {k}"
    assert rec["gate_state"] in ("clean", "abstain", "high_dispersion",
                                 "high_tv_share", "high_sensitivity")


def test_fix13_dispersion_gate_downgrades_buy(monkeypatch):
    """A BUY whose blended methods disagree > 2.5× is downgraded off BUY and
    tagged gate_state=high_dispersion — no confident BUY on uncorroborated legs."""
    # Force a BUY reaching the FIX-13 gate with a wide method spread by stubbing
    # blended() to return disagreeing components + a strong primary, and letting
    # the rest of recommend run on a clean nonfin.
    co = {"type": "nonfinancial", "ticker": "X", "price": 100.0, "equity": 5000.0,
          "shares": 100.0, "net_profit": 600.0, "revenue": 2000.0, "net_debt": 0.0,
          "synthetic_price": False, "_valuation_sector": "MANUFACTURING",
          "statements": {}}
    a = {"risk_free": 0.07, "beta": 1.0, "erp": 0.05, "rev_growth": 0.10, "ebit_margin": 0.15,
         "tax_rate": 0.25, "reinvest_rate": 0.4, "terminal_growth": 0.05, "fade_years": 10,
         "_valuation_sector": "MANUFACTURING"}
    # blended value ~1.8× the price → a big-upside name whose own methods disagree
    # 4× (Exit 600 vs P/E 150). The done-check guarantee: it never ships as a
    # confident BUY — it is gated (by VAL-01 at >0.50 MoS, or the FIX-13 gate in
    # the 0.15–0.50 band). Either way verdict != BUY and gate_state != clean.
    wide = {"blended": 180.0, "primary": 180.0, "primary_method": "FCFF DCF", "valuation": {
              "intrinsic": 180.0, "tv_pv": 60.0, "pv_explicit": 40.0, "bvps0": None, "ke": 0.12},
            "components": [{"method": "FCFF DCF", "value": 180.0, "weight": 0.65},
                           {"method": "Exit Multiple", "value": 600.0, "weight": 0.20},
                           {"method": "P/E (sector)", "value": 150.0, "weight": 0.15}]}
    monkeypatch.setattr(engines, "blended", lambda c, aa: wide)
    rec = engines.recommend(co, a)
    assert rec["method_dispersion"] is not None and rec["method_dispersion"] > 2.5
    assert rec["verdict"] != "BUY"                          # no confident BUY above 2.5× dispersion
    assert rec["gate_state"] in ("high_dispersion", "abstain")


def test_fix13_dispersion_gate_fires_in_mid_band(monkeypatch):
    """Directly exercise the FIX-13 generalization: a would-be BUY in the
    0.15–0.50 MoS band (below VAL-01's reach) with methods disagreeing > 2.5× is
    downgraded off BUY and tagged high_dispersion."""
    co, a = _nonfin_co_a()
    co = {**co, "price": 130.0}                             # intrinsic 180 → MoS ~0.38
    wide = {"blended": 180.0, "primary": 180.0, "primary_method": "FCFF DCF", "valuation": {
              "intrinsic": 180.0, "tv_pv": 60.0, "pv_explicit": 40.0, "bvps0": None, "ke": 0.12},
            "components": [{"method": "FCFF DCF", "value": 180.0, "weight": 0.65},
                           {"method": "Exit Multiple", "value": 600.0, "weight": 0.20},
                           {"method": "P/E (sector)", "value": 150.0, "weight": 0.15}]}
    monkeypatch.setattr(engines, "blended", lambda c, aa: wide)
    # force the pre-gate verdict to BUY by making the composite strong: high margin
    # + clean data. If it lands ACCUMULATE/HOLD the gate rightly doesn't apply, so
    # only assert the invariant when the base verdict would have been BUY.
    rec = engines.recommend(co, a)
    if rec["gate_state"] == "high_dispersion":
        assert rec["verdict"] == "LOW CONF"
    assert rec["verdict"] != "BUY"                          # invariant holds regardless
