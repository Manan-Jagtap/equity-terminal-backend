"""Batch 29 — DATA-10: identity-guard the lender P&L supplement.

The /historical_stats fuzzy name search carried no exchange code, so a bare-
ticker query could resolve to a STRANGER whose Sales-shaped lines were then
stored as the lender's own (J&KBANK carried a non-financial's Interest/opex;
19 lenders incl. BAJFINANCE/RECLTD/HUDCO were contaminated). Three guards:
query by verified name, require the lender payload shape, and anchor identity
on PBT agreement with the exchange-code-verified /stock rows."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ["DATABASE_URL"] = "sqlite:////tmp/_pytest_data10.db"

import pytest
from app import models
from app.database import Base, engine, SessionLocal
from app.ingest import indianapi_ingester as ing


@pytest.fixture()
def db():
    Base.metadata.create_all(engine)
    s = SessionLocal()
    yield s
    s.close()
    Base.metadata.drop_all(engine)


def _bank(db, ticker="J&KBANK", name="Jammu and Kashmir Bank"):
    co = models.Company(ticker=ticker, name=name, type="financial",
                        sector="Banks", template_code="BANK", shares_outstanding=100.0)
    db.add(co); db.commit()
    return co


_LENDER = {
    "Revenue":  {"Mar 2025": 12000.0, "Mar 2026": 13000.0},
    "Interest": {"Mar 2025": 7000.0,  "Mar 2026": 7500.0},
    "Expenses": {"Mar 2025": 3000.0,  "Mar 2026": 3200.0},
    "Profit before tax": {"Mar 2025": 2000.0, "Mar 2026": 2300.0},
}
_SALES_SHAPED = {           # the non-financial Screener shape (the J&KBANK hit)
    "Sales":    {"Mar 2025": 3401.0},
    "Expenses": {"Mar 2025": 2948.0},
    "OPM %":    {"Mar 2025": 13.0},
    "Interest": {"Mar 2025": 229.0},
    "Profit before tax": {"Mar 2025": 300.0},
}


def test_sales_shaped_payload_writes_nothing(db, monkeypatch):
    """The exact J&KBANK contamination: a Sales/OPM% payload for a BANK must be
    refused outright — no line of a stranger's P&L may be stored."""
    co = _bank(db)
    monkeypatch.setattr(ing, "_get_safe", lambda p, q: dict(_SALES_SHAPED))
    assert ing._financial_pl_supplement(db, co) == 0
    assert db.query(models.HistoricalFinancial).filter_by(company_id=co.id).count() == 0


def test_pbt_anchor_rejects_wrong_entity(db, monkeypatch):
    """Lender-shaped but the WRONG lender: PBT disagrees with the verified
    /stock rows on every overlapping year → identity unproven → nothing."""
    co = _bank(db)
    ing._upsert_hist(db, co.id, 2025, "PL", "pbt", 900.0)   # verified /stock pbt
    db.commit()
    monkeypatch.setattr(ing, "_get_safe", lambda p, q: dict(_LENDER))  # pbt 2000 ≠ 900
    assert ing._financial_pl_supplement(db, co) == 0
    assert db.query(models.HistoricalFinancial).filter_by(
        company_id=co.id, line_item="interest_income").count() == 0


def test_pbt_anchor_accepts_self_and_writes(db, monkeypatch):
    co = _bank(db)
    ing._upsert_hist(db, co.id, 2025, "PL", "pbt", 2000.0)  # matches payload
    db.commit()
    captured = {}
    def fake(path, params):
        captured.update(params); return dict(_LENDER)
    monkeypatch.setattr(ing, "_get_safe", fake)
    n = ing._financial_pl_supplement(db, co)
    db.commit()
    assert n > 0
    # queried by the VERIFIED NAME, not the bare ticker
    assert captured.get("stock_name") == "Jammu and Kashmir Bank"
    ii = db.query(models.HistoricalFinancial).filter_by(
        company_id=co.id, fiscal_year=2026, line_item="interest_income").first()
    assert ii is not None and ii.value == 13000.0
    nii = db.query(models.HistoricalFinancial).filter_by(
        company_id=co.id, fiscal_year=2026, line_item="nii").first()
    assert abs(nii.value - (13000.0 - 7500.0)) < 1e-6


def test_no_stored_pbt_overlap_still_writes(db, monkeypatch):
    """A fresh onboard has no /stock pbt rows yet — the anchor can't disprove
    identity, so the (shape-checked, name-queried) payload is accepted; this
    keeps first-time ingests working."""
    co = _bank(db)
    monkeypatch.setattr(ing, "_get_safe", lambda p, q: dict(_LENDER))
    assert ing._financial_pl_supplement(db, co) > 0
