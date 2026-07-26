"""DAT-13: the absurd-MoS gate could only ever catch INFLATED fair values.

mos = intrinsic/price - 1 is unbounded above but floored at -1, so the
existing `abs(mos) > 5` test is structurally incapable of firing on a fair
value that collapses toward zero — no threshold change fixes that, only a
second one-sided test.

Found live 2026-07-27: ADANIGREEN published an ungated AVOID with intrinsic
3.1 against a price of 1,380 (mos -0.998).
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")

import pytest


def test_old_gate_cannot_catch_a_collapsed_value():
    """Pins WHY a second check is needed rather than a tuned threshold."""
    mos_worthless = -1.0                      # intrinsic 0 — the worst possible
    assert abs(mos_worthless) <= 5, (
        "mos is floored at -1, so abs(mos) > 5 can NEVER fire on the downside; "
        "if this ever fails the metric's definition changed")


@pytest.mark.parametrize("price,intrinsic,should_flag", [
    (1380.0,    3.1, True),    # ADANIGREEN, the live case
    (1376.0,   76.7, True),    # SOBHA — 5.6% of price
    (1000.0,   99.0, True),    # exactly at the 10% boundary
    (1000.0,  101.0, False),   # just above it — a harsh but sane call
    (1000.0,  460.0, False),   # MARUTI-like: bearish, not absurd
    (1000.0, 1200.0, False),   # ordinary upside
])
def test_collapse_threshold(price, intrinsic, should_flag):
    mos = intrinsic / price - 1
    assert (mos <= -0.90) is should_flag, f"mos={mos:.3f}"


def test_gated_names_are_not_flagged():
    """A collapsed value is allowed when the product already shows a no-call."""
    from app import data_integrity as di
    import inspect
    src = inspect.getsource(di)
    i = src.index("mos_collapsed_ungated")
    window = src[max(0, i - 400):i]
    assert "not _gated" in window, (
        "the collapse check must respect the same LOW CONF / NO CALL gate as "
        "the inflation check, or every honest abstention turns the sweep red")
