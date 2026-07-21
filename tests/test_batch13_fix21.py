"""Batch 13 — FIX-21 (FM-04): portfolio-LEVEL risk controls. The risk layer used
to protect against bad names, not bad portfolios. Now: single-name ≤8% (trim
prompt at 10%), sector ≤25%, correlation guard (avg pairwise > 0.75 rejects an
add), portfolio-vol 12–18% band, and a −12% drawdown alert — surfaced in the PM
note."""
from app.portfolio_risk import annualised_vol, current_drawdown
from app.portfolio_routes import _risk_control_lines, build_analysis, compute_totals


# ── the risk math ─────────────────────────────────────────────────────────────

def test_annualised_vol_thin_and_real():
    assert annualised_vol([0.0] * 10) is None          # too few points
    assert annualised_vol([0.0] * 100) == 0.0          # no moves → 0
    v = annualised_vol([0.01, -0.01] * 60)             # ±1%/day
    assert 0.10 < v < 0.25                             # ~1% daily → ~16% annual


def test_current_drawdown_peak_and_dip():
    at_high = [("d1", 100), ("d2", 110), ("d3", 120)]
    assert current_drawdown(at_high) == 0.0            # sitting at the peak
    dipped = [("d1", 100), ("d2", 120), ("d3", 102)]   # 120 → 102 = −15%
    assert abs(current_drawdown(dipped) - (-0.15)) < 1e-9
    assert current_drawdown([("d1", 100)]) is None     # too short


# ── PM-note risk-control lines ────────────────────────────────────────────────

def _analysis(top1=None, sectors=None, vol=None, cdd=None, obs=250):
    return {"concentration": {"top1": top1} if top1 is not None else {},
            "sectors": sectors or [],
            "risk": {"vol_annual": vol, "current_drawdown": cdd, "observations": obs}}


def test_single_name_flag_fires_above_10pct():
    assert any("10% trim line" in l for l in _risk_control_lines(_analysis(top1=0.14)))
    assert _risk_control_lines(_analysis(top1=0.08)) == []       # 8% is fine


def test_sector_cap_flag_above_25pct():
    lines = _risk_control_lines(_analysis(sectors=[{"sector": "Banks", "weight": 0.31},
                                                   {"sector": "IT", "weight": 0.10}]))
    assert any("25% cap" in l and "Banks" in l for l in lines)


def test_vol_band_both_edges():
    assert any("above the 12–18%" in l for l in _risk_control_lines(_analysis(vol=0.24)))
    assert any("below the 12–18%" in l for l in _risk_control_lines(_analysis(vol=0.08)))
    assert not any("12–18%" in l for l in _risk_control_lines(_analysis(vol=0.15)))  # in band


def test_drawdown_alert_at_minus_12():
    assert any("Drawdown alert" in l for l in _risk_control_lines(_analysis(cdd=-0.15)))
    assert not any("Drawdown alert" in l for l in _risk_control_lines(_analysis(cdd=-0.05)))


def test_clean_book_no_flags():
    assert _risk_control_lines(_analysis(top1=0.06,
                                         sectors=[{"sector": "IT", "weight": 0.20}],
                                         vol=0.15, cdd=-0.03)) == []


# ── single-name trim prompt now fires at 10%, not 30% ─────────────────────────

def test_trim_prompt_fires_at_10pct_for_a_healthy_name():
    """A 20%-of-book HOLD with a fine valuation still gets a concentration trim
    prompt — the old 30% gate would have stayed silent."""
    items = [
        {"ticker": "BIG", "name": "Big", "sector": "IT", "value": 20000.0, "cost": 15000.0,
         "mos": 0.05, "verdict": "HOLD", "term": "long", "days_to_lt": 0, "confidence": "HIGH"},
        {"ticker": "SM1", "name": "Small1", "sector": "Banks", "value": 40000.0, "cost": 30000.0,
         "mos": 0.05, "verdict": "HOLD", "term": "long", "days_to_lt": 0, "confidence": "HIGH"},
        {"ticker": "SM2", "name": "Small2", "sector": "Pharma", "value": 40000.0, "cost": 30000.0,
         "mos": 0.05, "verdict": "HOLD", "term": "long", "days_to_lt": 0, "confidence": "HIGH"},
    ]
    compute_totals(items)   # BIG = 20% of ₹1L
    analysis = build_analysis(items, [])
    big = next((r for r in analysis["recommendations"] if r["ticker"] == "BIG"), None)
    assert big is not None
    assert any("10% single-name trim line" in x for x in big["reasons"])
