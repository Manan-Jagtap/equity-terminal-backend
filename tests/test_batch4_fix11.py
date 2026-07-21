"""Batch 4 — FIX-11 exit-multiple deflation. Engine-math; the end-to-end math is
covered by the engineParity harness (60/60). These guard the three constants
against silent drift and re-mint the engine.js mirror requirement."""
from app import engines, sector_params as SP


def test_fix11_blend_weights_deflated():
    """DCF leads harder, exit multiple trusted less."""
    nonfin = dict(engines._BLEND_WEIGHTS["nonfin"])
    assert nonfin["FCFF DCF"] == 0.65
    assert nonfin["Exit Multiple"] == 0.20
    assert nonfin["P/E (sector)"] == 0.15
    assert abs(sum(nonfin.values()) - 1.0) < 1e-9


def test_fix11_exit_pe_rebased():
    assert SP.SECTOR_PARAMS["CONSUMER"]["exit_pe"] == 34
    assert SP.SECTOR_PARAMS["CONSUMER_DISC"]["exit_pe"] == 30
    assert SP.SECTOR_PARAMS["CAPITAL_GOODS"]["exit_pe"] == 27
    assert SP.SECTOR_PARAMS["DEFENCE"]["exit_pe"] == 28


def test_fix11_crosscheck_clamp_tightened():
    """A rich cross-check (2× the primary) must be capped at 1.6× the primary in
    the blend — the tightened [0.6, 1.6] band. Build a nonfin whose exit multiple
    is deliberately far above the DCF and confirm the blend can't run away."""
    co = {"type": "nonfinancial", "ticker": "T", "sector": "Capital Goods",
          "revenue": 1000.0, "net_profit": 120.0, "net_debt": 0.0, "shares": 100.0,
          "price": 50.0, "_valuation_sector": "CAPITAL_GOODS",
          "statements": {}, "synthetic_price": False}
    a = {"risk_free": 0.07, "beta": 1.0, "erp": 0.05, "fade_years": 10,
         "rev_growth": 0.10, "ebit_margin": 0.14, "tax_rate": 0.25,
         "reinvest_rate": 0.4, "terminal_growth": 0.05, "_valuation_sector": "CAPITAL_GOODS"}
    b = engines.blended(co, a)
    primary = b.get("primary")
    if primary and b.get("components"):
        for comp in b["components"]:
            if comp["value"] is not None and comp["method"] != b.get("primary_method"):
                # capped value never exceeds 1.6× (nor below 0.6×) the primary
                assert comp["value"] <= 1.6 * primary + 1e-6
                assert comp["value"] >= 0.6 * primary - 1e-6
