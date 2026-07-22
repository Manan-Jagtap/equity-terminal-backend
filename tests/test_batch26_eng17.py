"""Batch 26 — FIX-28 tail (part 6): ENG-17 AMFI-style rank-based cap tiers.
Tiers follow the AMFI convention (rank within the covered universe: top 100
large, 101-250 mid, 251+ small) instead of a ₹-static cut; the ₹ bounds
survive only as fallbacks for unranked names + the liquidity floor."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")

from app.hidden_gems import (_cap_tier, evaluate_name,
                             RANK_LARGE, RANK_MID, CAP_MAX_CR, CAP_MIN_CR)


def _ev(mos=0.2, quality=80, growth=0.15):
    """Minimal evidence blob that clears every non-cap hard gate."""
    return {
        "name": "Test Co", "sector": "IT", "price": 100.0,
        "growth": growth,
        "quality": {"composite": quality, "flags": []},
        "model": {"mos": mos, "confidence": "MEDIUM", "verdict": "BUY"},
        "results": {"pat_yoy": 0.2},
        "momentum": {"trend": "up"},
        "consensus": {},
    }


def test_tier_by_rank_overrides_rupee_cut():
    # a ₹25,000 Cr name ranked 180 is an AMFI mid cap, not "Large cap"
    assert _cap_tier(25000.0, cap_rank=180) == "Mid cap"
    assert _cap_tier(25000.0, cap_rank=50) == "Large cap"
    assert _cap_tier(4000.0, cap_rank=400) == "Small cap"
    # boundary semantics: rank 100 large, 101 mid, 250 mid, 251 small
    assert _cap_tier(None, cap_rank=RANK_LARGE) == "Large cap"
    assert _cap_tier(None, cap_rank=RANK_LARGE + 1) == "Mid cap"
    assert _cap_tier(None, cap_rank=RANK_MID) == "Mid cap"
    assert _cap_tier(None, cap_rank=RANK_MID + 1) == "Small cap"


def test_tier_rupee_fallback_when_unranked():
    assert _cap_tier(4000.0) == "Small cap"
    assert _cap_tier(15000.0) == "Mid cap"
    assert _cap_tier(25000.0) == "Large cap"
    assert _cap_tier(None) is None


def test_gate_rank_biased_ruled_by_rank_not_rupees():
    ev = _ev()
    # rank ≤ 100 → excluded even at a "mid-cap-looking" ₹ size
    assert evaluate_name("T1", ev, 15000.0, 0.2, 0.15, 0.2, False,
                         cap_rank=90) is None
    # rank 180 at ₹25k Cr (above the old ₹-static max) → now legitimately IN
    gem = evaluate_name("T2", ev, 25000.0, 0.2, 0.15, 0.2, False, cap_rank=180)
    assert gem is not None
    assert gem["cap_tier"] == "Mid cap" and gem["cap_rank"] == 180


def test_gate_rupee_fallback_and_floor_still_hold():
    ev = _ev()
    # unranked: the old ₹-static max still gates
    assert evaluate_name("T3", ev, CAP_MAX_CR + 1, 0.2, 0.15, 0.2, False) is None
    # the ₹ liquidity floor applies even with a small-cap rank
    assert evaluate_name("T4", ev, CAP_MIN_CR - 1, 0.2, 0.15, 0.2, False,
                         cap_rank=900) is None


def test_quality_gates_unweakened_by_rank():
    """Regression: NO gate-loosening — a red-flag or low-quality name stays out
    regardless of how attractive its rank is."""
    bad = _ev(quality=40)
    assert evaluate_name("T5", bad, 5000.0, 0.2, 0.15, 0.2, False,
                         cap_rank=300) is None
    thin = _ev()
    thin["model"]["confidence"] = "LOW"
    assert evaluate_name("T6", thin, 5000.0, 0.2, 0.15, 0.2, False,
                         cap_rank=300) is None
