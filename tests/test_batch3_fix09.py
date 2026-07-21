"""Batch 3 — FIX-09 margin-based DISTRIBUTION-economics override. Backend-only
classification (engine.js takes _valuation_sector as input) → parity untouched."""
import os
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_batch3_test.db")
import pytest
from app.database import Base, engine, SessionLocal
from app import models, assemble


@pytest.fixture()
def db():
    Base.metadata.drop_all(engine); Base.metadata.create_all(engine)
    s = SessionLocal()
    yield s
    s.close()


def _mk(s, ticker, sector, *, equity, years):
    """years = {fy: (revenue, pat)} → HistoricalFinancial PL rows + equity fact."""
    co = models.Company(ticker=ticker, name=ticker, type="nonfinancial", sector=sector,
                        shares_outstanding=100.0)
    s.add(co); s.flush()
    s.add(models.FinancialFact(company_id=co.id, fiscal_year=max(years), concept="NET_WORTH", value=equity))
    s.add(models.FinancialFact(company_id=co.id, fiscal_year=max(years), concept="REVENUE",
                               value=years[max(years)][0]))
    for fy, (rev, pat) in years.items():
        s.add(models.HistoricalFinancial(company_id=co.id, fiscal_year=fy, statement_type="PL",
                                         line_item="revenue", value=rev))
        s.add(models.HistoricalFinancial(company_id=co.id, fiscal_year=fy, statement_type="PL",
                                         line_item="pat", value=pat))
    s.add(models.MarketSnapshot(company_id=co.id, price=100.0))
    s.commit()
    return co


# thin net margin (1.3%), high revenue (₹60k cr), capital-light (6× turnover) → distributor
_DISTRIBUTOR = dict(equity=10000.0, years={2024: (60000.0, 780.0), 2025: (60000.0, 780.0)})
# thin net margin (3.5%) but LOW turnover (2×) — owns capacity → brand, NOT distribution
_BRAND = dict(equity=6000.0, years={2024: (12000.0, 420.0), 2025: (12000.0, 420.0)})


def test_fix09_distributor_rebucketed(db):
    co = _mk(db, "DISTTEST", "Consumer", **_DISTRIBUTOR)
    data = assemble.build_company(db, co)
    assert data["valuation_sector"] == "DISTRIBUTION"
    assert data.get("_distribution_override")


def test_fix09_brand_low_turnover_not_rebucketed(db):
    """Thin net margin but < 2.5× revenue/equity turnover → a brand, kept in
    its full-margin sector (this is what stopped UBL/VOLTAS being under-valued)."""
    co = _mk(db, "BRANDTEST", "Consumer", **_BRAND)
    data = assemble.build_company(db, co)
    assert data["valuation_sector"] != "DISTRIBUTION"
    assert not data.get("_distribution_override")


def test_fix09_cyclical_guard_metal_untouched(db):
    """A cyclical (METAL) with a thin down-cycle margin is NEVER re-bucketed —
    only CONSUMER/_DISC/MANUFACTURING are eligible."""
    co = _mk(db, "METALTEST", "Steel", **_DISTRIBUTOR)   # same thin/high-turnover profile
    data = assemble.build_company(db, co)
    assert data["valuation_sector"] == "METAL"
    assert not data.get("_distribution_override")


def test_fix09_near_zero_margin_excluded(db):
    """Near-zero / breakeven net margin (ABDL-class) is a loss-maker case the
    low-ROE gate abstains, not distribution economics — excluded by the floor."""
    co = _mk(db, "ZEROTEST", "Consumer",
             equity=10000.0, years={2024: (60000.0, 60.0), 2025: (60000.0, 60.0)})  # 0.1% npm
    data = assemble.build_company(db, co)
    assert data["valuation_sector"] != "DISTRIBUTION"


def test_fix09_through_cycle_npm_median():
    npm, rev = assemble._through_cycle_npm({2023: {"PL": {"revenue": 1000.0, "pat": 20.0}},
                                            2024: {"PL": {"revenue": 2000.0, "pat": 60.0}}})
    assert abs(npm - 0.025) < 1e-9      # median of [0.02, 0.03]
    assert rev == 1500.0                # median of [1000, 2000]
    assert assemble._through_cycle_npm({}) == (None, None)
