"""Batch 2 — FIX-10 (corroboration-aware lender gate + segment EV/EBITDA basis)
and FIX-12 (type↔sector reconciliation). Backend-only; no engine.js surface."""
import os
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_batch2_test.db")
import pytest
from app import engines, segment_sotp, sector_params as SP
from app.database import Base, engine, SessionLocal
from app import models, assemble


@pytest.fixture()
def db():
    Base.metadata.drop_all(engine); Base.metadata.create_all(engine)
    s = SessionLocal()
    yield s
    s.close()


def _mk(s, ticker, sector, ctype, *, equity=10000.0, net_profit=1500.0, shares=100.0):
    co = models.Company(ticker=ticker, name=ticker, type=ctype, sector=sector,
                        shares_outstanding=shares)
    s.add(co); s.flush()
    s.add(models.FinancialFact(company_id=co.id, fiscal_year=2026, concept="NET_WORTH", value=equity))
    s.add(models.FinancialFact(company_id=co.id, fiscal_year=2026, concept="NET_PROFIT", value=net_profit))
    s.add(models.MarketSnapshot(company_id=co.id, price=50.0))
    s.commit()
    return co


# ── FIX-12: type↔sector reconciliation ───────────────────────────────────────
def test_fix12_financial_vsector_forces_financial_type(db):
    """A name whose sector resolves to a financial valuation_sector but is stored
    type=nonfinancial (the 12 mis-typed brokers) is reconciled to financial, so
    it runs the book/RI path (nbfc block) and NOT the FCFF path (revenue/net_debt)."""
    co = _mk(db, "TESTBROKER", "Investment Services", "nonfinancial")
    data = assemble.build_company(db, co)
    assert data["valuation_sector"] in SP.FINANCIAL_VSECTORS
    assert data["type"] == "financial"          # reconciled
    assert "nbfc" in data                        # financial block populated
    assert "revenue" not in data                 # FCFF fields NOT populated


def test_fix12_nonfinancial_untouched(db):
    """A real non-financial (manufacturing) keeps its type — the invariant only
    fires for financial valuation_sectors, never re-buckets an industrial."""
    co = _mk(db, "TESTMFG", "Steel", "nonfinancial")
    data = assemble.build_company(db, co)
    assert data["valuation_sector"] not in SP.FINANCIAL_VSECTORS
    assert data["type"] == "nonfinancial"
    assert "revenue" in data and "nbfc" not in data


# ── FIX-10: corroboration-aware lender gate ───────────────────────────────────
def _fin_co(price):
    return {"type": "financial", "ticker": "TESTLENDER", "sector": "Banks",
            "equity": 10000.0, "net_profit": 1500.0, "shares": 100.0,
            "price": price, "nbfc": {}, "synthetic_price": False,
            "_valuation_sector": "BANK"}


def _fin_a(terminal_roe, forecast_roe=0.16):
    return {"risk_free": 0.07, "beta": 1.0, "erp": 0.05, "payout": 0.2, "fade_years": 10,
            "forecast_roe": forecast_roe, "terminal_roe": terminal_roe,
            "terminal_growth": 0.05, "rev_growth": 0.10, "_valuation_sector": "BANK"}


def test_fix10_corroboration_predicate():
    """The exact quantity the ≥80%-MoS lender gate now conditions on: the
    Gordon-growth justified P/B value vs 1.25× price. A strong terminal ROE
    corroborates (≥1.25× price); a weak one does not — so the same MoS lands a
    banded call in the first case and LOW CONF in the second. (The end-to-end
    conversion is verified empirically on UNIONBANK: LOW CONF→ACCUMULATE, while
    the >100%-MoS PSU lenders stay abstained via the separate implausibility gate.)"""
    co = _fin_co(price=50.0)
    strong = engines.gordon_pb_value(co, _fin_a(terminal_roe=0.16), engines.valuate(co, _fin_a(0.16)))
    weak = engines.gordon_pb_value(co, _fin_a(terminal_roe=0.055), engines.valuate(co, _fin_a(0.055)))
    assert strong is not None and strong >= 1.25 * co["price"]   # corroborated → call kept
    assert weak is None or weak < 1.25 * co["price"]             # uncorroborated → LOW CONF as before
    assert strong > (weak or 0)                                  # monotone in terminal ROE


def test_fix10_no_corroborating_leg_cannot_clear_gate():
    """When the justified-P/B leg can't be computed (no terminal ROE), the gate
    has nothing to corroborate on, so a ≥80%-MoS lender stays abstained — the
    conversion is EARNED by a second leg, never granted by default."""
    co = _fin_co(price=50.0)
    a = _fin_a(terminal_roe=None)
    assert engines.gordon_pb_value(co, a, engines.valuate(co, a)) is None


# ── FIX-10: segment SOTP EV/EBITDA basis ──────────────────────────────────────
def test_fix10_segment_prefers_ebitda_basis():
    v_ebitda, how_e = segment_sotp._segment_value({"ebitda": 100, "sector": "CONSUMER"})
    v_ebit, how_i = segment_sotp._segment_value({"ebit": 100, "sector": "CONSUMER"})
    assert v_ebitda == v_ebit                     # same multiple, correct basis available
    assert "EBITDA" in how_e
    assert "conservative floor" in how_i          # EBIT path is labelled a floor


def test_fix10_quarters_stale():
    import datetime as dt
    old = (dt.date.today() - dt.timedelta(days=400)).strftime("%Y-%m")
    assert segment_sotp._quarters_stale(old) >= 4
    assert segment_sotp._quarters_stale(dt.date.today().strftime("%Y-%m")) == 0
    assert segment_sotp._quarters_stale("not-a-date") is None
