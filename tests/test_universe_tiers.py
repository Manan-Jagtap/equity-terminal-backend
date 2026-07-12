"""
tests/test_universe_tiers.py — locks the tiered-visibility invariants.
Run: python tests/test_universe_tiers.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")
os.environ.setdefault("INDIANAPI_KEY", "test")

from app.ingest.indianapi_ingester import (
    NIFTY_50, NIFTY_NEXT_50, NIFTY_MIDCAP_150, NIFTY_500_REST, EXTRA_TICKERS,
    EXCLUDED_TICKERS, CORE_UNIVERSE, VISIBLE_UNIVERSE, UNIVERSE_TIER, _TIERS,
)


def test_tranches_are_disjoint():
    assert not (NIFTY_50 & NIFTY_NEXT_50)
    assert not ((NIFTY_50 | NIFTY_NEXT_50) & NIFTY_MIDCAP_150)
    assert not ((NIFTY_50 | NIFTY_NEXT_50 | NIFTY_MIDCAP_150) & NIFTY_500_REST)
    print("  ok tranches disjoint")


def test_tiers_are_nested():
    assert _TIERS["nifty100"] < _TIERS["nifty250"] < _TIERS["nifty500"]
    print("  ok tiers nested")


def test_tier_sizes_sane():
    assert len(_TIERS["nifty100"]) == 103              # 50 + 50 + owner extras (FEDFINA, RATEGAIN, IDEAFORGE)
    assert 230 <= len(_TIERS["nifty250"]) <= 260
    assert 480 <= len(_TIERS["nifty500"]) <= 520
    print("  ok tier sizes")


def test_core_universe_is_the_quota_priced_set():
    assert CORE_UNIVERSE == NIFTY_50 | NIFTY_NEXT_50 | EXTRA_TICKERS
    print("  ok core universe")


def test_excluded_never_visible():
    assert "INDIGO" in EXCLUDED_TICKERS
    for tier_set in _TIERS.values():
        assert not (tier_set - EXCLUDED_TICKERS) & EXCLUDED_TICKERS
    assert not VISIBLE_UNIVERSE & EXCLUDED_TICKERS
    print("  ok exclusions applied")


def test_default_tier_is_nifty100():
    # This test runs without UNIVERSE_TIER set, so the safe default must hold.
    # INDIGO is officially IN the Nifty 50 now, so the exclusion actively strips
    # it: visible = core minus held-back names, and never equals bare core.
    if not os.getenv("UNIVERSE_TIER"):
        assert UNIVERSE_TIER == "nifty100"
        assert VISIBLE_UNIVERSE == CORE_UNIVERSE - EXCLUDED_TICKERS
        assert "INDIGO" in CORE_UNIVERSE and "INDIGO" not in VISIBLE_UNIVERSE
    print("  ok default tier")


if __name__ == "__main__":
    test_tranches_are_disjoint()
    test_tiers_are_nested()
    test_tier_sizes_sane()
    test_core_universe_is_the_quota_priced_set()
    test_excluded_never_visible()
    test_default_tier_is_nifty100()
    print("test_universe_tiers: all passed")
