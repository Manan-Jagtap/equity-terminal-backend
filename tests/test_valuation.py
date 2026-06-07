"""
tests/test_valuation.py — regression tests pinning the INDEPENDENT valuation
engine and the bug fixes from the June 2026 audit.

Run:
  pip install pytest
  python -m pytest tests/ -q
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")

from app.database import Base, engine, SessionLocal
from app import models, engines
from app.assemble import build_company, effective_assumptions
from app.templates import classify_company
from app.financials import build_financials_response
from app.sector_params import classify_valuation_sector, cost_of_equity, params
from app.derive import derive_assumptions


# ── Fixtures ────────────────────────────────────────────────────────────────
def _it_statements():
    st, base = {}, 180000
    for i, yr in enumerate(range(2020, 2027)):
        rev = base * (1.09 ** i)
        st[yr] = {
            "PL": {"revenue": round(rev), "ebit": round(rev*0.245), "ebitda": round(rev*0.27),
                   "pat": round(rev*0.19), "pbt": round(rev*0.25), "tax": round(rev*0.06)},
            "BS": {"net_worth": round(rev*0.40), "total_assets": round(rev*0.55),
                   "total_liabilities": round(rev*0.15), "borrowings": round(rev*0.02)},
            "CF": {"operating_cf": round(rev*0.2), "capex": round(-rev*0.02), "pat": round(rev*0.19)},
        }
    return st


def _bank_statements():
    return {
        2022: {"PL": {"pat": 38052, "pbt": 50873, "tax": 12722},
               "BS": {"net_worth": 247326, "equity": 554, "reserves": 183072, "borrowings": 240060}, "CF": {"dividends": -3592}},
        2023: {"PL": {"pat": 45997, "pbt": 61498, "tax": 15349},
               "BS": {"net_worth": 289437, "equity": 557, "borrowings": 268339}, "CF": {"dividends": -8604}},
        2024: {"PL": {"pat": 64062, "pbt": 76568, "tax": 11122},
               "BS": {"net_worth": 456395, "equity": 759, "borrowings": 744548}, "CF": {"dividends": -8404}},
        2025: {"PL": {"pat": 70792, "pbt": 96242, "tax": 22801},
               "BS": {"net_worth": 521789, "equity": 765, "borrowings": 648259}, "CF": {"dividends": -14826}},
        2026: {"PL": {"pat": 76025, "pbt": 102141, "tax": 22921},
               "BS": {"net_worth": 586059, "equity": 1539, "borrowings": 588484}, "CF": {"dividends": -20705, "fcf": 109616}},
    }


@pytest.fixture(scope="module")
def db():
    if os.path.exists("/tmp/_pytest_terminal.db"):
        os.remove("/tmp/_pytest_terminal.db")
    Base.metadata.create_all(bind=engine)
    s = SessionLocal()

    def add(ticker, name, sector, shares, price, stmts, facts):
        tmpl = classify_company(name, sector)
        co = models.Company(ticker=ticker, name=name, sector=sector,
                            type="financial" if tmpl in ("BANK", "NBFC", "INSURANCE") else "nonfinancial",
                            template_code=tmpl, shares_outstanding=shares)
        s.add(co); s.flush()
        s.add(models.MarketSnapshot(company_id=co.id, price=price))
        for yr, d in stmts.items():
            for stmt, items in d.items():
                for k, v in items.items():
                    s.add(models.HistoricalFinancial(company_id=co.id, fiscal_year=yr,
                                                     statement_type=stmt, line_item=k, value=v))
        for k, v in facts.items():
            s.add(models.FinancialFact(company_id=co.id, fiscal_year=2026, period="FY", concept=k, value=v))
        s.commit(); return co

    add("TCS", "Tata Consultancy Services Ltd.", "Information Technology", 363.6, 2199,
        _it_statements(), {"NET_WORTH": 107240, "NET_PROFIT": 49454, "REVENUE": 267021, "NET_DEBT": -32664})
    add("HDFCBANK", "HDFC Bank Ltd.", "Financial Services", 765.0, 2000,
        _bank_statements(), {"NET_WORTH": 586059, "NET_PROFIT": 76025})
    yield s
    s.close()


def _recommend(db, ticker):
    co = db.query(models.Company).filter_by(ticker=ticker).first()
    data = build_company(db, co)
    a = effective_assumptions(db, co, data)
    return co, data, a, engines.recommend(data, a)


# ── Classification ──────────────────────────────────────────────────────────
def test_bank_classified_as_bank_not_nbfc():
    assert classify_company("HDFC Bank Ltd.", "Financial Services") == "BANK"
    assert classify_company("State Bank of India", "Financial Services") == "BANK"
    assert classify_company("Bajaj Finance", "Financial Services") == "NBFC"
    assert classify_company("SBI Life Insurance", "Insurance") == "INSURANCE"


def test_valuation_sector_splits_manufacturing():
    assert classify_valuation_sector("Automobiles", "MANUFACTURING") == "AUTO"
    assert classify_valuation_sector("Steel", "MANUFACTURING") == "METAL"
    assert classify_valuation_sector("Cement", "MANUFACTURING") == "CEMENT"
    assert classify_valuation_sector("Telecom Services", "MANUFACTURING") == "TELECOM"
    # explicit BANK template beats the generic 'financial services' string
    assert classify_valuation_sector("Financial Services", "BANK") == "BANK"


def test_cost_of_equity_in_sane_band():
    for code in ("IT_SERVICES", "BANK", "NBFC", "METAL", "CONSUMER"):
        ke = cost_of_equity(params(code)["beta"])
        assert 0.10 <= ke <= 0.14, f"{code} Ke={ke}"


# ── Independent valuation ───────────────────────────────────────────────────
def test_no_trim_verdict_ever(db):
    for t in ("TCS", "HDFCBANK"):
        _, _, _, rec = _recommend(db, t)
        assert rec["verdict"] != "TRIM"
        assert rec["verdict"] in {"BUY", "ACCUMULATE", "HOLD", "REDUCE", "AVOID", "NO DATA", "LOW CONF"}


def test_tcs_independent_intrinsic_reasonable(db):
    _, data, a, rec = _recommend(db, "TCS")
    iv, price = rec["intrinsic"], data["price"]
    assert rec["valuation"]["method"] == "FCFF DCF"
    assert a["_valuation_sector"] == "IT_SERVICES"
    # independent DCF should land within a sane band of the market (not 21% low)
    assert 0.70 * price <= iv <= 1.35 * price, f"TCS iv={iv} price={price}"


def test_bank_uses_residual_income_and_real_roe(db):
    co, data, a, rec = _recommend(db, "HDFCBANK")
    assert co.template_code == "BANK"
    assert rec["valuation"]["method"] == "Residual Income"
    # ROE must be realistic (net worth based), NOT the 68-92% share-capital bug
    assert 0.10 <= a["forecast_roe"] <= 0.20, a["forecast_roe"]


def test_bank_roe_and_fcf_in_financials(db):
    co = db.query(models.Company).filter_by(ticker="HDFCBANK").first()
    rows = db.query(models.HistoricalFinancial).filter_by(company_id=co.id).all()
    fr = build_financials_response(co, rows)
    y = max(fr["years_available"])
    roe = fr["statements"][y]["PL"].get("roe")
    assert roe is not None and 0.08 <= roe <= 0.25, f"bank ROE={roe}"   # not 0.49-0.92
    assert "fcf" not in fr["statements"][y].get("CF", {})               # FCF suppressed for banks


def test_assumptions_are_history_derived_not_flat(db):
    # beta must vary by sector (the old yfinance path was a flat ~1.0 for all)
    _, _, a_it, _ = _recommend(db, "TCS")
    _, _, a_bk, _ = _recommend(db, "HDFCBANK")
    assert a_it["beta"] != a_bk["beta"]
    assert a_it["ebit_margin"] > 0.18   # derived from the IT history (~24%)


# ── Validation tie-outs ─────────────────────────────────────────────────────
def test_validation_runs(db):
    from app.validation import validate_statements, validation_summary
    co = db.query(models.Company).filter_by(ticker="TCS").first()
    rows = db.query(models.HistoricalFinancial).filter_by(company_id=co.id).all()
    fr = build_financials_response(co, rows)
    summary = validation_summary(validate_statements(fr["statements"], fr["is_financial"]))
    assert summary["n_run"] >= 1
