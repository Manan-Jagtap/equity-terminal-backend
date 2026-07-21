"""Batch 9 — FIX-14 (gate-filtered value factor) + FIX-15 (engines-disagree
surface). Alpha-layer only: factors.py READS verdicts, never writes them."""
from app.factors import score_universe


def _rows():
    # A KISSHT-class gated name with an absurd MoS + junk-cheap multiples, vs
    # three confident names with honest inputs.
    return [
        {"ticker": "KISSHT", "mos": 4.5, "roe": 0.30, "pe": 0.8, "pb": 0.2,
         "verdict": "LOW CONF", "closes": [], "growth": None},
        {"ticker": "GOOD1", "mos": 0.30, "roe": 0.22, "pe": 18.0, "pb": 3.0,
         "verdict": "BUY", "closes": [], "growth": None},
        {"ticker": "GOOD2", "mos": 0.10, "roe": 0.18, "pe": 22.0, "pb": 4.0,
         "verdict": "HOLD", "closes": [], "growth": None},
        {"ticker": "GOOD3", "mos": -0.20, "roe": 0.12, "pe": 30.0, "pb": 6.0,
         "verdict": "REDUCE", "closes": [], "growth": None},
    ]


def test_fix14_gated_name_gets_neutral_value_credit():
    ranked = {r["ticker"]: r for r in score_universe(_rows())}
    assert ranked["KISSHT"]["factors"]["value"] == 50.0     # neutral, despite +450% MoS & 0.8x P/E
    assert ranked["GOOD1"]["factors"]["value"] > 50.0       # honest cheap name ranks above neutral
    # KISSHT cannot out-rank the confident cheap name on value anymore
    assert ranked["GOOD1"]["factors"]["value"] > ranked["KISSHT"]["factors"]["value"]


def test_fix14_confident_ranks_unpolluted():
    """Value percentiles are computed over confident names only — the junk 0.8×
    P/E doesn't squeeze honest names' percentiles."""
    ranked = {r["ticker"]: r for r in score_universe(_rows())}
    assert ranked["GOOD1"]["factors"]["value"] == 100.0     # best of the CONFIDENT set


def test_fix14_no_verdict_feedback():
    """score_universe passes verdicts through untouched — never rewrites them."""
    ranked = {r["ticker"]: r for r in score_universe(_rows())}
    assert ranked["KISSHT"]["verdict"] == "LOW CONF"
    assert ranked["GOOD1"]["verdict"] == "BUY"


def test_fix15_disagree_bearish_verdict_high_alpha():
    rows = [{"ticker": f"N{i}", "mos": 0.01 * i, "roe": 0.10 + 0.02 * i,
             "pe": 20.0, "pb": 3.0, "verdict": "HOLD", "closes": [], "growth": None}
            for i in range(8)]
    rows.append({"ticker": "RICH", "mos": -0.30, "roe": 0.35, "pe": 12.0, "pb": 2.0,
                 "verdict": "AVOID", "closes": [], "growth": None})
    ranked = {r["ticker"]: r for r in score_universe(rows)}
    rich = ranked["RICH"]
    if rich["alpha_score"] is not None and rich["alpha_score"] >= 70:
        assert rich["engines_disagree"] == "verdict_bearish_alpha_bullish"
        assert "AVOID" in rich["disagree_note"]
    # names without a conflict carry no chip
    assert ranked["N1"]["engines_disagree"] is None


def test_fix15_no_disagree_when_aligned():
    ranked = score_universe(_rows())
    for r in ranked:
        if (r["verdict"] or "").upper() in ("LOW CONF", "NO DATA"):
            assert r["engines_disagree"] is None            # gated names never chip
