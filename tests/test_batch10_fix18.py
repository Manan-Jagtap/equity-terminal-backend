"""Batch 10 — FIX-18 verdict re-banding (CORR-5/VAL-10), calibrated empirically
against the 1,001-name groundtruth: AVOID from −18%, BUY capped at +50%.
Verdict logic is backend-only (no JS mirror); the intraday _verdict_from bands
must stay in lockstep with engines.recommend."""
from app.ingest.compute_valuations import _verdict_from


def test_fix18_avoid_from_18():
    # −20% is AVOID now (was REDUCE); −15% stays REDUCE; −10% stays HOLD
    assert _verdict_from(-0.20, 50, True, "AVOID") == "AVOID"
    assert _verdict_from(-0.15, 50, True, "REDUCE") == "REDUCE"
    assert _verdict_from(-0.10, 50, True, "HOLD") == "HOLD"


def test_fix18_buy_capped_at_50():
    # strong composite, +40% → BUY; +55% → steps down to ACCUMULATE (same zone)
    assert _verdict_from(0.40, 75, True, "BUY") == "BUY"
    assert _verdict_from(0.55, 75, True, "BUY") == "ACCUMULATE"


def test_fix18_sticky_states_unchanged():
    assert _verdict_from(0.40, 75, True, "LOW CONF") == "LOW CONF"   # sticky abstain
    assert _verdict_from(None, 75, True, "HOLD") == "NO DATA"
    assert _verdict_from(0.40, 75, False, "HOLD") == "LOW CONF"      # unreliable


def test_fix18_bands_match_recommend_ladder():
    """The intraday mirror and the batch ladder share thresholds — probe the
    exact boundary values on both sides' semantics."""
    # boundary: −0.18 is REDUCE (>=), just below is AVOID
    assert _verdict_from(-0.18, 50, True, "x") == "REDUCE"
    assert _verdict_from(-0.181, 50, True, "x") == "AVOID"
    # boundary: +0.50 is BUY (<=), just above steps down
    assert _verdict_from(0.50, 75, True, "x") == "BUY"
    assert _verdict_from(0.501, 75, True, "x") == "ACCUMULATE"
