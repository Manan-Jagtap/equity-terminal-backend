"""Batch 8 — FIX-17 evidence-earned compounder terminal ROE (VAL-02/VAL-09).
Paired derive.py ↔ derive.js (48-case derive parity). The lift is EARNED and
one-sided: only a STABLE proven franchise in a mild dip gets it."""
from app.derive import derive_assumptions


def _fin_stmts(roes, nw=1000.0):
    """Financial statements giving a per-year ROE series (PAT/NetWorth)."""
    return {2020 + i: {"PL": {"pat": r * nw}, "BS": {"net_worth": nw}}
            for i, r in enumerate(roes)}


def test_fix17_stable_depressed_franchise_lifts():
    """A durable ~15% franchise in a mild dip (HDFC-Bank signature: latest 0.13
    vs franchise ~0.16) reverts a bounded step ABOVE its depressed forecast."""
    a = derive_assumptions(_fin_stmts([0.16, 0.16, 0.15, 0.14, 0.13]), "BANK", True)
    assert "FIX-17" in a["_drivers"]["terminal_roe"]
    assert a["terminal_roe"] > a["forecast_roe"]        # terminal now exceeds the depressed forecast
    assert a["terminal_roe"] <= a["forecast_roe"] + 0.03 + 1e-9   # bounded recovery
    assert a["terminal_roe"] <= 0.18                    # capped


def test_fix17_boom_bust_not_lifted():
    """A name whose 'franchise' is an unrepeatable boom-year peak (latest 0.145
    vs franchise ~0.20 → ratio < 0.80) is NOT lifted — no windfall."""
    a = derive_assumptions(_fin_stmts([0.14, 0.30, 0.20, 0.15, 0.145]), "BANK", True)
    assert "FIX-17" not in a["_drivers"]["terminal_roe"]
    assert a["terminal_roe"] <= a["forecast_roe"] + 1e-9


def test_fix17_weak_lender_not_lifted():
    """A low-ROE lender (median < 1.2×Ke) never earns the lift — the LIC-HF guard
    stays intact (terminal cannot exceed forecast)."""
    a = derive_assumptions(_fin_stmts([0.08, 0.09, 0.10, 0.11, 0.12]), "BANK", True)
    assert "FIX-17" not in a["_drivers"]["terminal_roe"]
    assert a["terminal_roe"] <= a["forecast_roe"] + 1e-9


def test_fix17_thin_history_not_lifted():
    """Fewer than 4 years of ROE → no persistence evidence → no lift."""
    a = derive_assumptions(_fin_stmts([0.16, 0.15]), "BANK", True)
    assert "FIX-17" not in a["_drivers"]["terminal_roe"]


def test_fix17_one_sided_never_lowers():
    """The lift can only raise the terminal; a name that doesn't qualify keeps the
    existing min(forecast, …) terminal exactly."""
    base = derive_assumptions(_fin_stmts([0.08, 0.09, 0.10, 0.11, 0.12]), "BANK", True)
    assert base["terminal_roe"] <= base["forecast_roe"] + 1e-9
