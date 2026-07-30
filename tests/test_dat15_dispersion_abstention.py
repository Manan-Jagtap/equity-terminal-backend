"""DAT-15: extreme method dispersion withholds the number, keeps the call.

VAL-04 only defended the BUY side — when the engine's methods disagreed it
refused a confident BUY but still published a precise fair value on the AVOID
side. Ground truth (2026-07-30, 10 mid-caps valued blind to the engine) showed
that number is the unreliable half: the engine sat 42-80% BELOW every band while
the DIRECTION was right every time.

Also pins the contract that made the first cut of this incomplete: the screener
reads the PERSISTED gate_state, so a new withholding gate must be registered in
main.SUPPRESSING_GATES or the company page hides the number while the screener
still shows it.
"""
import inspect
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")

from app import engines


def test_gate_is_symmetric_not_buy_side_only():
    src = inspect.getsource(engines.recommend)
    assert "DAT-15" in src and "_disp > 4.0" in src


def test_requires_direction_unanimity():
    """If the legs straddle the price, direction isn't corroborated either —
    that is a no-call (VAL-04's job), not a silent withhold."""
    src = inspect.getsource(engines.recommend)
    i = src.index("DAT-15")
    j = src.index("DAT-13:", i)          # window = the whole DAT-15 block
    assert 'max(_wv) < co["price"]' in src[i:j]


def test_dat13_cannot_reset_dat15():
    """Ordering bug guard: DAT-13's `value_suppressed = False` used to sit
    BELOW DAT-15 and would have silently undone it."""
    src = inspect.getsource(engines.recommend)
    assert src.index("value_suppressed = False") < src.index("DAT-15"), (
        "value_suppressed must be initialised ABOVE DAT-15, or DAT-13 resets it")


def test_alt_model_names_are_exempt():
    """Preset-driven names carry hand-seeded marks; dispersion means something
    different there and they are outside THE CLAIM anyway."""
    src = inspect.getsource(engines.recommend)
    i = src.index("DAT-15")
    j = src.index("DAT-13:", i)
    assert "not alt" in src[i:j]


def test_suppressing_gates_contract():
    """EVERY gate that sets value_suppressed=True must be registered for the
    screener, which reads the persisted gate_state rather than the live flag."""
    from app.main import SUPPRESSING_GATES
    src = inspect.getsource(engines.recommend)
    gates = set()
    for m in re.finditer(r"value_suppressed = True", src):
        window = src[max(0, m.start() - 700):m.start() + 700]
        gates |= set(re.findall(r'_gate = "(\w+)"', window))
    assert gates, "no value_suppressed sites found — regex drifted"
    missing = gates - set(SUPPRESSING_GATES)
    assert not missing, (
        f"gates {sorted(missing)} withhold the number in recommend() but are not "
        f"in main.SUPPRESSING_GATES — the screener will still publish it")
