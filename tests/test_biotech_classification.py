"""CORR-3a: 'Biotechnology & Drugs' must classify as PHARMA, not IT_SERVICES.

The sector rules are first-match-wins over a lowercased substring, and the
generic ("technology", "IT_SERVICES") sat AHEAD of ("biotech", "PHARMA").
"BioTECHNOLOGY & Drugs" — the vendor's sector string for 31 live names —
therefore matched the IT rule first, and every one of those pharma companies
was valued on IT_SERVICES economics (mature_roic 0.30 vs pharma's 0.18, which
inflates terminal value).

Fix: the specific pharma/healthcare keywords now precede the generic IT block.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")

import pytest
from app.sector_params import classify_valuation_sector as cls


@pytest.mark.parametrize("raw", [
    "Biotechnology & Drugs",
    "biotechnology and drugs",
    "Biotech",
])
def test_biotech_is_pharma_not_it(raw):
    assert cls(raw) == "PHARMA", f"{raw!r} must not fall through to IT_SERVICES"


@pytest.mark.parametrize("raw,expected", [
    ("Information Technology", "IT_SERVICES"),
    ("IT Services & Consulting", "IT_SERVICES"),
    ("Software & Programming", "IT_SERVICES"),
    ("Internet Services", "IT_SERVICES"),
    ("Hospitals", "PHARMA"),
    ("Healthcare Facilities", "PHARMA"),
    ("Medical Equipment & Supplies", "PHARMA"),
    ("Pharmaceuticals", "PHARMA"),
])
def test_no_regression_in_neighbouring_sectors(raw, expected):
    """Moving the pharma block up must not steal genuine IT names."""
    assert cls(raw) == expected


def test_rule_order_pharma_precedes_generic_technology():
    """Guard the ORDERING itself — the bug was position, not a missing rule."""
    import inspect
    from app import sector_params
    src = inspect.getsource(sector_params)
    # Match the RULE LINES (indented tuples), not prose in the comments — the
    # explanatory comment above the block also names ("technology", …).
    i_biotech = src.index('\n    ("biotech", "PHARMA")')
    i_tech = src.index('\n    ("information technology", "IT_SERVICES")')
    assert i_biotech < i_tech, (
        "the specific ('biotech', PHARMA) rule must precede the generic "
        "('technology', IT_SERVICES) rule — first match wins")


def test_pharma_and_it_params_actually_differ():
    """Pins WHY the misclassification mattered: the two archetypes price
    differently, so a wrong label is a wrong valuation, not a cosmetic tag."""
    from app.sector_params import params
    assert params("PHARMA")["mature_roic"] != params("IT_SERVICES")["mature_roic"]
