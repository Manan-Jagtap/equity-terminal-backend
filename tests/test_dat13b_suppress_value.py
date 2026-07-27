"""DAT-13b: keep a corroborated AVOID, suppress the meaningless number.

PR #81 turned all 62 collapsed-value names into LOW CONF. That was honest
about the NUMBER but discarded the DIRECTION — and 23 of those AVOIDs agreed
with independent ground truth. Here the two claims are separated: the point
estimate is withheld, the verdict survives IF an independent leg corroborates
it, and an uncorroborated collapse still abstains.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")

import pytest
from app.engines import collapse_corroborated
from app.main import apply_value_suppression, FAIR_VALUE_NM


def _co(price=1000.0, net_debt=0.0, shares=1.0):
    return {"price": price, "net_debt": net_debt, "shares": shares}


def test_relative_leg_below_price_corroborates():
    ok, why = collapse_corroborated(
        _co(), {"_valuation_sector": "REALTY"}, {},
        [{"method": "Exit Multiple", "value": 600.0}])
    assert ok and "Exit Multiple" in why


def test_relative_leg_ABOVE_price_does_not_corroborate():
    """The leg must AGREE the name is expensive, not merely exist."""
    ok, _ = collapse_corroborated(
        _co(), {"_valuation_sector": "REALTY"}, {},
        [{"method": "Exit Multiple", "value": 1500.0}])
    assert not ok


def test_leverage_leg_corroborates():
    """Net debt above the entire equity market value — the thin residual."""
    ok, why = collapse_corroborated(
        _co(price=100.0, net_debt=500.0, shares=1.0),
        {"_valuation_sector": "REALTY"}, {}, [])
    assert ok and "net debt" in why


def test_premium_to_book_on_subpar_returns_corroborates():
    from app.sector_params import params
    mature = params("MANUFACTURING")["mature_roe"]
    ok, why = collapse_corroborated(
        _co(), {"_valuation_sector": "MANUFACTURING"},
        {"pb": 4.0, "roe": mature * 0.5}, [])
    assert ok and "book" in why


def test_nothing_corroborates_means_abstain():
    ok, why = collapse_corroborated(
        _co(), {"_valuation_sector": "MANUFACTURING"},
        {"pb": 1.2, "roe": 0.30}, [{"method": "Exit Multiple", "value": 5000.0}])
    assert not ok and why is None


# ── the leak test the spec called non-optional ────────────────────────────────

def test_suppression_nulls_every_number_field():
    out = apply_value_suppression(
        {"intrinsic": 3.1, "mos": -0.998, "blended": 3.1, "verdict": "AVOID"}, True)
    assert out["intrinsic"] is None and out["mos"] is None and out["blended"] is None
    assert out["verdict"] == "AVOID", "the CALL must survive — that is the point"
    assert out["fair_value_note"] == FAIR_VALUE_NM


def test_suppression_is_idempotent():
    p = {"intrinsic": 3.1, "mos": -0.998, "verdict": "AVOID"}
    assert apply_value_suppression(apply_value_suppression(p, True), True)["mos"] is None


def test_unsuppressed_payload_is_untouched():
    p = {"intrinsic": 2949.0, "mos": 0.308, "verdict": "BUY"}
    out = apply_value_suppression(dict(p), False)
    assert out == p and "value_suppressed" not in out


def test_engine_still_carries_the_raw_number():
    """Suppression is a PRESENTATION contract — the batch, the calibration
    harness and the integrity sweep all need the raw intrinsic. If the engine
    ever nulls it, those three go blind."""
    import inspect
    from app import engines
    src = inspect.getsource(engines.recommend)
    i = src.index("value_suppressed = True")
    window = src[i:i + 600]
    assert 'mos = None' not in window and 'iv = None' not in window


def test_corroboration_leg_attribution_is_documented():
    """Pins the swept finding: only the relative-multiple leg fires today.

    If premium-to-book or leverage ever starts corroborating a real name, the
    thresholds beside them stop being unreachable insurance and become live
    free parameters — at which point they need an actual sweep, not the
    hand-picked values they carry now. This test is the tripwire for that.
    """
    import inspect
    from app import engines
    src = inspect.getsource(engines)
    assert "SWEPT 2026-07-27" in src, (
        "the sweep finding must stay documented beside the thresholds — "
        "they look calibrated and are not")


def test_thresholds_are_still_the_swept_values():
    from app import engines
    assert (engines._CORR_PB, engines._CORR_ROE_SHORTFALL,
            engines._CORR_NETDEBT_MCAP) == (3.0, 1.0, 1.0)
