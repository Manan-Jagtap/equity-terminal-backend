"""CORR-3c: transport/logistics names must not inherit factory economics.

MANUFACTURING is DEFAULT_SECTOR, so any name the keyword classifier cannot
place lands there. The vendor sends the bare string "Services" for ports,
container rail and express parcel — unplaceable by keyword, exactly the
REDINGTON failure mode — so these are pinned per-ticker.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")

import pytest
from app.sector_params import TICKER_OVERRIDES, DEFAULT_SECTOR, params

LOGISTICS_NAMES = ["ADANIPORTS", "CONCOR", "BLUEDART", "JSWINFRA", "TVSSCS"]


@pytest.mark.parametrize("tk", LOGISTICS_NAMES)
def test_transport_names_pinned_to_logistics(tk):
    assert TICKER_OVERRIDES.get(tk) == "LOGISTICS", (
        f"{tk} is a transport/logistics business; without the pin it falls "
        f"through to {DEFAULT_SECTOR} and is valued on factory economics")


@pytest.mark.parametrize("tk", ["MHRIL", "WONDERLA"])
def test_leisure_names_deliberately_not_reclassified(tk):
    """Guards a deliberate omission, not an oversight.

    Both are plainly not manufacturers, but CONSUMER_DISC is the RICHER
    archetype and both already value ABOVE their ground-truth band, so the
    move made them worse (MHRIL x2.72 -> x3.19). Anyone 'completing' the
    reclassification later should have to delete this test on purpose.
    """
    assert tk not in TICKER_OVERRIDES
    assert params("CONSUMER_DISC")["mature_roic"] > params(DEFAULT_SECTOR)["mature_roic"]


def test_logistics_and_manufacturing_price_differently():
    """A wrong label is a wrong valuation, not a cosmetic tag."""
    a, b = params("LOGISTICS"), params(DEFAULT_SECTOR)
    assert (a["mature_roic"], a["exit_pe"]) != (b["mature_roic"], b["exit_pe"])
