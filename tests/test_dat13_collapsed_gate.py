"""DAT-13 gate: a COLLAPSED fair value must abstain, not publish a number.

Found live 2026-07-27 while verifying the CORR-1 deploy: ADANIGREEN served
an ungated AVOID with intrinsic 3.1 against a price of 1,380 (mos -0.998).
That is the model asserting the equity is worth 0.2% of its market price —
not a bearish call but a broken number, published as a precise fair value.

The gate lives in price_gates() so BOTH the batch engine and the intraday
price-tick path apply it (VAL-03): a price move must never be able to
un-gate a name.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")

import pytest
from app.engines import price_gates


def _g(mos, verdict="AVOID", **kw):
    kw.setdefault("is_financial", False)
    kw.setdefault("is_alt", False)
    kw.setdefault("high_roe", False)
    return price_gates(verdict, mos, **kw)


@pytest.mark.parametrize("tk,price,iv", [
    ("ADANIGREEN", 1379.9,   3.1),   # the live case: 0.2% of price
    ("SOBHA",      1376.1,  76.7),   # 5.6% of price
])
def test_collapsed_values_abstain(tk, price, iv):
    assert _g(iv / price - 1) == "LOW CONF", f"{tk} must not publish a bare fair value"


@pytest.mark.parametrize("tk,price,iv", [
    ("PRESTIGE",   1630.1, 430.5),   # -74%: harsh, but a real call
    ("JSWENERGY",   566.2, 170.5),   # -70%
    ("MARUTI",    13442.0, 6165.1),  # -54%
])
def test_genuine_bearish_calls_survive(tk, price, iv):
    """The gate must not swallow ordinary conviction — only broken numbers."""
    assert _g(iv / price - 1) == "AVOID", f"{tk} is a real AVOID, not an artifact"


def test_boundary_is_ten_percent_of_price():
    assert _g(-0.90) == "LOW CONF"       # intrinsic exactly 10% of price
    assert _g(-0.8999) == "AVOID"        # a hair above


def test_gate_is_not_limited_to_high_roe():
    """Unlike the -0.45 cliff, this one must fire on ORDINARY-return names.

    The names it catches are levered infra/realty developers whose equity is a
    thin residual after debt — precisely the cohort with unremarkable ROIC.
    """
    assert _g(-0.95, high_roe=False) == "LOW CONF"


def test_it_abstains_rather_than_reverses():
    """The name stays uninvestable; we just stop quoting a precise number."""
    assert _g(-0.95) not in ("BUY", "ACCUMULATE", "HOLD")


def test_none_mos_is_untouched():
    assert _g(None) == "AVOID"
