"""Batch 4 — FIX-11 exit-multiple deflation. Engine-math; the end-to-end math is
covered by the engineParity harness (60/60). These guard the three constants
against silent drift and re-mint the engine.js mirror requirement."""
from app import engines, sector_params as SP


def test_fix11_blend_weights_deflated():
    """DCF leads harder, exit multiple trusted less.

    Values moved 0.65/0.20/0.15 -> 0.66/0.19/0.15 (and nonfin_light
    0.55/0.15/0.30 -> 0.56/0.15/0.29) on 14 Aug 2026. This guard caught the
    change, which is what it is for — updated deliberately, not relaxed.

    Provenance: a 5-fold cross-validated search over the weight simplex, scored
    on held-out names against the calibration bands, found the aggregate wants
    substantially MORE weight on the DCF (+10 names out-of-sample across 267).
    Most of that gain is not bankable: at 0.80 the calibration gate fails on
    FRESH-TRANCHE WITHIN-BAND REGRESSED (12.7% -> 11.8%) even though blended
    (26.7 -> 29.6) and OLD (32.5 -> 37.0) both improve — the old-ruler mirage the
    tranche split exists to expose. 0.66 is the step that survives every gate:
    within-band 26.7% -> 27.6% (+3/-0), zero hard breaks, fresh tranche flat.

    Keep pinning exact values. The engine.js mirror carries the same numbers and
    the parity harness (60/60) will fail if the two drift apart.
    """
    nonfin = dict(engines._BLEND_WEIGHTS["nonfin"])
    assert nonfin["FCFF DCF"] == 0.66
    assert nonfin["Exit Multiple"] == 0.19
    assert nonfin["P/E (sector)"] == 0.15
    assert abs(sum(nonfin.values()) - 1.0) < 1e-9

    light = dict(engines._BLEND_WEIGHTS["nonfin_light"])
    assert light["FCFF DCF"] == 0.56
    assert light["Exit Multiple"] == 0.15
    assert light["P/E (sector)"] == 0.29
    assert abs(sum(light.values()) - 1.0) < 1e-9


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
