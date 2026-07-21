"""Batch 11 — FIX-19 (FM-02): the Fund Manager consumes the full FIX-13
valuation contract and HARD-gates on it. A non-clean gate_state, methods
disagreeing > 2.5×, or thin (low) data → ineligible for the public ledger AND
conviction capped at 60, so AUBANK/PATELENG-class names can never top the desk.
Absent (None) contract signals are treated as NO signal — a pre-FIX-13 stored
row must never silently empty the ledger."""
from app.manager_engine import (
    _valuation_uncertain, conviction_add, triangulate, PRIOR_WEIGHTS,
)


# ── the eligibility predicate ────────────────────────────────────────────────

def test_uncertain_on_nonclean_gate():
    assert _valuation_uncertain({"gate_state": "high_dispersion"}) is True
    assert _valuation_uncertain({"gate_state": "high_tv_share"}) is True
    assert _valuation_uncertain({"gate_state": "abstain"}) is True


def test_uncertain_on_dispersion_and_thin_tier():
    assert _valuation_uncertain({"method_dispersion": 16.3}) is True   # AUBANK
    assert _valuation_uncertain({"data_tier": "low"}) is True


def test_clean_and_absent_signals_are_certain():
    assert _valuation_uncertain({"gate_state": "clean", "data_tier": "high"}) is False
    assert _valuation_uncertain({"gate_state": "clean", "data_tier": "medium"}) is False
    # a pre-FIX-13 row: everything None → NOT uncertain (never empties the ledger)
    assert _valuation_uncertain({}) is False
    assert _valuation_uncertain(None) is False
    assert _valuation_uncertain({"method_dispersion": 1.4}) is False


# ── conviction cap ───────────────────────────────────────────────────────────

def _strong_ev(model):
    """An evidence row with genuinely strong legs (cheap + high quality +
    uptrend) that WOULD earn > 60 conviction on its own."""
    return {
        "tri": {"score": 0.55, "used": ["model", "consensus", "band"], "suspect": False},
        "quality": {"composite": 82, "grade": "A", "red_flags": []},
        "momo": {"above_200dma": True, "above_50dma": True, "mom_pct": 80,
                 "off_52w_high": -0.03},
        "flow": {}, "results": {}, "growth": 0.14, "sector": "IT",
        "model": model,
    }


def test_strong_name_earns_high_conviction_when_clean():
    cv, _ = conviction_add(_strong_ev({"gate_state": "clean", "data_tier": "high"}),
                           PRIOR_WEIGHTS, None)
    assert cv > 60, f"a clean strong name should clear 60, got {cv}"


def test_uncertain_valuation_caps_conviction_at_60():
    # identical strong legs, but the valuation contract is broken → capped
    for bad in ({"gate_state": "high_dispersion"},
                {"method_dispersion": 16.3},
                {"data_tier": "low"}):
        cv, reasons = conviction_add(_strong_ev(bad), PRIOR_WEIGHTS, None)
        assert cv <= 60, f"{bad} should cap at 60, got {cv}"
        assert any("capped at 60" in r for r in reasons)


# ── model-vote down-weight in triangulation ──────────────────────────────────

def test_dispersion_halves_model_vote():
    """With three agreeing witnesses, a > 2.5× dispersion halves the model
    weight, pulling the blended score toward the other two — a lower score than
    the same inputs with no dispersion flag."""
    # all three witnesses agree "cheap" (so the model isn't set aside by the
    # democratic gate); dispersion is the only thing that differs.
    args = dict(cons_upside=0.20, band_pct=25.0, confidence="MEDIUM",
                intrinsic=100.0, weights=PRIOR_WEIGHTS, sector_trust=0.6)
    clean = triangulate(0.60, **args, method_dispersion=1.2)
    noisy = triangulate(0.60, **args, method_dispersion=8.0)
    assert clean["score"] is not None and noisy["score"] is not None
    assert noisy["score"] < clean["score"]
    assert any("down-weighted" in r for r in noisy["suspect_reasons"])
