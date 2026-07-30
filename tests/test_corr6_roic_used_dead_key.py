"""REGISTERED DEFECT: CORR-6's reinvestment floor reads a key that is never set.

app/engines.py fcff_dcf:
    _roic_ex = (a.get("roic_used") or a.get("terminal_roic")
                or SP.params(a.get("_valuation_sector")).get("mature_roic") or 0.12)
    ...
    rr_t = max(rr_t, min(g / _roic_ex, _REINVEST_CAP))

`derive_assumptions` never puts "roic_used" in its return dict — the value exists
only as a LOCAL inside derive.py, where the growth ceiling and the g/ROIC identity
use it correctly. So `a.get("roic_used")` is always None, `terminal_roic` is
usually None too, and the floor silently divides by the SECTOR's mature_roic.

Measured 2026-07-30: KAYNES falls back to 0.17, TCS to 0.30 — sector constants,
not either company's realised return. CORR-6's stated intent was the opposite.

NOT FIXED HERE, deliberately. A multi-agent test of the naive repair (export the
key) measured it as strictly one-directional UP across 37.4% of non-financials,
with the largest beneficiaries being large-cap quality names — GPPL 7.27x,
VOLTAMP 6.48x, SIEMENS 6.40x — i.e. it re-inflates exactly the compounder cohort
the ledger has repeatedly flagged for upward level bias. Shipping that to repair
a mid-cap complaint would trade one bias for a bigger one.

The strict-xfail below fails the build if someone "fixes" the export without
re-measuring the book. Read this file first, then run the calibration harness.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")

import inspect

import pytest

from app import derive, engines


def test_the_consumer_still_reads_the_key():
    """Pins the consumer side. If this changes, the defect may be moot."""
    src = inspect.getsource(engines.fcff_dcf)
    assert 'a.get("roic_used")' in src, (
        "CORR-6's reinvestment floor no longer reads roic_used — re-verify "
        "whether this registered defect still applies")


@pytest.mark.xfail(strict=True, reason=(
    "REGISTERED DEFECT: derive_assumptions never exports roic_used, so CORR-6's "
    "reinvestment floor silently uses the SECTOR mature_roic. Fixing the export "
    "is one-directional UP across ~37% of non-financials and re-inflates large-cap "
    "compounders (SIEMENS 6.4x, VOLTAMP 6.5x) — do not repair without re-measuring "
    "the calibration book and mirroring in derive.js for the 48/48 parity contract."))
def test_derive_exports_roic_used():
    src = inspect.getsource(derive)
    i = src.index("def derive_assumptions")
    body = src[i:]
    assert '"roic_used"' in body, (
        "roic_used is computed in derive.py but never returned, so every "
        "consumer reading a['roic_used'] gets None")
