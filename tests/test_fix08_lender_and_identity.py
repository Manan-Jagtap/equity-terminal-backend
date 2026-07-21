"""Batch 1 — FIX-08 (per-year lender P&L via yoy_results) + identity-refresh gap.

FIX-08: the supplement hit /statement stats=profit_loss (invalid → errors for
every lender) and wrote only the latest year, so HDFCBANK/ICICIBANK/SBIN carried
zero interest/NII/opex. Now it reads the annual yoy_results table and writes all
years. Identity-refresh: a curated row still carrying a past mis-resolution's
name/sector self-heals when the vendor confirms the ticker."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ["DATABASE_URL"] = "sqlite:////tmp/_pytest_fix08.db"

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


def _bank(db, ticker="HDFCBANK", tmpl="BANK"):
    co = models.Company(ticker=ticker, name=ticker, type="financial",
                        sector="Banks", template_code=tmpl, shares_outstanding=100.0)
    db.add(co); db.commit()
    return co


# yoy_results shape confirmed live for HDFCBANK/SHRIRAMFIN
_YOY = {
    "Revenue":      {"Mar 2024": 300000.0, "Mar 2025": 336367.0, "Mar 2026": 348615.0, "TTM": 351819.0},
    "Interest":     {"Mar 2024": 160000.0, "Mar 2025": 183894.0, "Mar 2026": 185491.0, "TTM": 185408.0},
    "Expenses":     {"Mar 2024": 150000.0, "Mar 2025": 186974.0, "Mar 2026": 203635.0},
    "Other Income": {"Mar 2024": 120000.0, "Mar 2025": 134548.0, "Mar 2026": 146848.0},
    "Net Profit":   {"Mar 2024": 60000.0,  "Mar 2025": 73440.0,  "Mar 2026": 79219.0},
}


def test_fix08_writes_lender_lines_for_every_year(db, monkeypatch):
    co = _bank(db)
    monkeypatch.setattr(ing, "_get_safe", lambda path, params: dict(_YOY))
    n = ing._financial_pl_supplement(db, co)
    db.commit()
    assert n > 0
    years = {r.fiscal_year for r in db.query(models.HistoricalFinancial)
             .filter_by(company_id=co.id, line_item="interest_income")}
    assert years == {2024, 2025, 2026}          # all years, not just latest; TTM skipped
    # NII derived per year: Revenue − Interest
    nii26 = db.query(models.HistoricalFinancial).filter_by(
        company_id=co.id, fiscal_year=2026, line_item="nii").first()
    assert abs(nii26.value - (348615.0 - 185491.0)) < 1e-6
    # opex + other_income present
    for li in ("interest_expense", "opex", "other_income"):
        assert db.query(models.HistoricalFinancial).filter_by(
            company_id=co.id, fiscal_year=2025, line_item=li).first() is not None


def test_fix08_additive_never_clobbers(db, monkeypatch):
    co = _bank(db)
    ing._upsert_hist(db, co.id, 2026, "PL", "interest_income", 999.0)  # pre-existing
    db.commit()
    monkeypatch.setattr(ing, "_get_safe", lambda path, params: dict(_YOY))
    ing._financial_pl_supplement(db, co); db.commit()
    kept = db.query(models.HistoricalFinancial).filter_by(
        company_id=co.id, fiscal_year=2026, line_item="interest_income").first()
    assert kept.value == 999.0                   # additive: not overwritten


def test_fix08_bad_year_skipped_visibly_and_insurer_noop(db, monkeypatch):
    co = _bank(db)
    bad = {"Revenue": {"Mar 2025": 100.0}, "Interest": {"Mar 2025": 200.0}}  # ii<=ie
    monkeypatch.setattr(ing, "_get_safe", lambda p, q: dict(bad))
    ing._financial_pl_supplement(db, co); db.commit()
    assert db.query(models.HistoricalFinancial).filter_by(
        company_id=co.id, fiscal_year=2025, line_item="interest_income").first() is None
    # insurer path returns 0 without touching yoy
    ins = _bank(db, "SBILIFE", tmpl="INSURANCE")
    assert ing._financial_pl_supplement(db, ins) == 0


def test_fix08_error_payload_logs_and_returns_zero(db, monkeypatch):
    co = _bank(db)
    monkeypatch.setattr(ing, "_get_safe", lambda p, q: {"error": "select a valid statement"})
    assert ing._financial_pl_supplement(db, co) == 0


def test_identity_refresh_trigger():
    def stock(name, nse):
        return {"companyName": name, "companyProfile": {"exchangeCodeNse": nse}}
    # APOLLO-class: vendor confirms ticker, stored name is a different entity
    assert ing._should_refresh_identity(stock("Apollo Micro Systems", "APOLLO"), "APOLLO", "Apollo Hospitals Enterprise")
    # correctly-curated: vendor name == stored → no refresh
    assert not ing._should_refresh_identity(stock("Tata Consultancy Services", "TCS"), "TCS", "Tata Consultancy Services")
    # Ltd/suffix variant → no churn
    assert not ing._should_refresh_identity(stock("HDFC Bank Ltd", "HDFCBANK"), "HDFCBANK", "HDFC Bank")
    # vendor NSE code disagrees with ticker → never refresh on an unconfirmed identity
    assert not ing._should_refresh_identity(stock("Something Else", "OTHER"), "APOLLO", "Apollo Hospitals")
