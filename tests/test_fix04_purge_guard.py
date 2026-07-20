"""FIX-04 / DATA-02 — parse-and-validate BEFORE purge (atomic statement swap).

`ingest_company` used to delete ALL of a company's HistoricalFinancial rows and
only THEN parse the fresh payload — so a vendor response with a price but an
empty/absent financials block wiped a good multi-year history to zero (SBILIFE
observed wiped live). `_swap_statements` now stages purge+parse+insurer-fallback
in a SAVEPOINT and rolls it back when the payload yields nothing usable, so an
empty/partial payload leaves the prior history intact.

The SBILIFE *restore* is a data re-ingest (owner env) — these tests prove the
guard that makes that restore, and every future re-ingest, safe."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ["DATABASE_URL"] = "sqlite:////tmp/_pytest_fix04.db"

import pytest

from app.ingest import indianapi_ingester as ing
from app.database import Base, engine, SessionLocal
from app import models


@pytest.fixture()
def db():
    Base.metadata.create_all(engine)
    s = SessionLocal()
    yield s
    s.close()
    Base.metadata.drop_all(engine)


def _company(db, ticker="SBILIFE"):
    co = models.Company(ticker=ticker, name=ticker, type="nonfinancial",
                        sector="X", shares_outstanding=100.0)
    db.add(co)
    db.commit()
    return co


def _seed_history(db, co, years):
    """One PL/revenue row per fiscal year — a stand-in for a real statement set."""
    for y in years:
        db.add(models.HistoricalFinancial(
            company_id=co.id, fiscal_year=y, statement_type="PL",
            line_item="revenue", value=1000.0 + y, source="screener"))
    db.commit()


def _count(db, co):
    return (db.query(models.HistoricalFinancial)
              .filter_by(company_id=co.id).count())


def test_empty_payload_preserves_prior_history(db, monkeypatch):
    """The SBILIFE failure mode: fresh payload has no statements → keep the 5
    prior years rather than wiping to zero."""
    co = _company(db)
    _seed_history(db, co, [2020, 2021, 2022, 2023, 2024])
    assert _count(db, co) == 5

    monkeypatch.setattr(ing, "_parse_financials", lambda s, cid, c, stock: 0)
    monkeypatch.setattr(ing, "_insurer_statements", lambda s, c: 0)

    n = ing._swap_statements(db, co, {"financials": []})
    db.commit()
    assert n == 0
    assert _count(db, co) == 5          # history intact — nothing overwritten


def test_good_payload_replaces_history(db, monkeypatch):
    """A real payload swaps the statements: old purged, new written, committed."""
    co = _company(db)
    _seed_history(db, co, [2018, 2019])          # 2 stale years

    def fake_parse(s, cid, c, stock):
        for y in (2022, 2023, 2024):             # 3 fresh years
            s.add(models.HistoricalFinancial(
                company_id=cid, fiscal_year=y, statement_type="PL",
                line_item="revenue", value=5000.0 + y, source="screener"))
        s.flush()
        return 3
    monkeypatch.setattr(ing, "_parse_financials", fake_parse)
    monkeypatch.setattr(ing, "_insurer_statements", lambda s, c: 0)

    n = ing._swap_statements(db, co, {"financials": [1]})
    db.commit()
    assert n == 3
    years = {r.fiscal_year for r in
             db.query(models.HistoricalFinancial).filter_by(company_id=co.id)}
    assert years == {2022, 2023, 2024}           # old gone, new present


def test_insurer_fallback_counts_as_usable(db, monkeypatch):
    """0 direct statements but the insurer /statement fallback writes rows → the
    swap holds (not rolled back), and the count reflects the fallback."""
    co = _company(db, "HDFCLIFE")
    _seed_history(db, co, [2020, 2021])

    def fake_insurer(s, c):
        for y in (2023, 2024):
            s.add(models.HistoricalFinancial(
                company_id=c.id, fiscal_year=y, statement_type="PL",
                line_item="premium", value=42.0, source="statement"))
        s.flush()
        return 2
    monkeypatch.setattr(ing, "_parse_financials", lambda s, cid, c, stock: 0)
    monkeypatch.setattr(ing, "_insurer_statements", fake_insurer)

    n = ing._swap_statements(db, co, {"financials": []})
    db.commit()
    assert n == 2
    years = {r.fiscal_year for r in
             db.query(models.HistoricalFinancial).filter_by(company_id=co.id)}
    assert years == {2023, 2024}


def test_first_ingest_with_empty_payload_is_a_noop(db, monkeypatch):
    """No prior history + empty payload: nothing to preserve, no error."""
    co = _company(db, "NEWCO")
    monkeypatch.setattr(ing, "_parse_financials", lambda s, cid, c, stock: 0)
    monkeypatch.setattr(ing, "_insurer_statements", lambda s, c: 0)

    n = ing._swap_statements(db, co, {"financials": []})
    db.commit()
    assert n == 0
    assert _count(db, co) == 0


def test_parser_exception_rolls_back_and_preserves_history(db, monkeypatch):
    """A mid-parse crash must not leave the company with a half-purged history."""
    co = _company(db)
    _seed_history(db, co, [2020, 2021, 2022])

    def boom(s, cid, c, stock):
        raise ValueError("garbled payload")
    monkeypatch.setattr(ing, "_parse_financials", boom)

    with pytest.raises(ValueError):
        ing._swap_statements(db, co, {"financials": [1]})
    db.rollback()
    assert _count(db, co) == 3          # savepoint rollback preserved the 3 years
