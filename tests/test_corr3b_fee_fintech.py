"""CORR-3b: payments/fintech FEE businesses must not be priced as lenders.

NBFC sat at 0/7 within-band. The cohort turned out to be a grab-bag rather than
one broken model:
  * genuine lenders   — MUTHOOTFIN (gold loan), HUDCO (housing finance)  → RI ok
  * holding companies — BAJAJHLDNG, ABCAPITAL                            → SOTP ok
  * NOT lenders       — ZAGGLE (prepaid/spend-management SaaS) and STYL
                        (Seshaasai, payments & card manufacturing)

The vendor files those last two under "Consumer Financial Services", so they
inherited the NBFC archetype and were valued with BOOK-based Residual Income —
wrong by construction for a capital-light fee annuity.

They are listed PER-TICKER deliberately: a sector hint on "consumer financial
services" would also swallow genuine consumer lenders such as BAJFINANCE.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")

import pytest
from app.engines import _is_fee_financial, _FEE_FINANCIALS, _FEE_FINANCIAL_SECTOR_HINTS


@pytest.mark.parametrize("ticker", ["ZAGGLE", "STYL"])
def test_fintech_fee_businesses_are_flagged(ticker):
    assert _is_fee_financial({"ticker": ticker,
                              "sector": "Consumer Financial Services"}) is True


@pytest.mark.parametrize("ticker", ["BAJFINANCE", "MUTHOOTFIN", "HUDCO", "SHRIRAMFIN"])
def test_genuine_lenders_are_not_flagged(ticker):
    """The whole reason for a per-ticker list: real lenders must keep the
    book-based model. A sector-hint approach would break exactly this."""
    assert _is_fee_financial({"ticker": ticker,
                              "sector": "Consumer Financial Services"}) is False


def test_consumer_financial_services_is_not_a_sector_hint():
    """Guard the DESIGN choice, not just today's behaviour: if someone later
    adds 'consumer financial' to the hints, genuine lenders silently become
    fee-financials and stop being valued."""
    joined = " ".join(_FEE_FINANCIAL_SECTOR_HINTS).lower()
    assert "consumer financial" not in joined
    assert "gold loan" not in joined
    assert "housing finance" not in joined


def test_existing_fee_financials_untouched():
    """The prior list must survive — this change is additive only."""
    for t in ("CRISIL", "BSE", "MCX", "CDSL", "HDFCAMC", "PAYTM", "ANGELONE"):
        assert t in _FEE_FINANCIALS
