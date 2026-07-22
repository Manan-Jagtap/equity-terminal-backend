"""Batch 23 — FIX-28 tail (part 3): INTG-04 statement freshness. Statement rows
now carry `updated_at`, stamped on write, so fundamentals staleness is measurable
at rest (prices/insights already had timestamps)."""
from app.database import SessionLocal, engine, Base
from app import models
from app.ingest.indianapi_ingester import _upsert_fact, _upsert_hist


def _company(s, cid, ticker):
    if not s.query(models.Company).filter_by(id=cid).first():
        s.add(models.Company(id=cid, ticker=ticker, name=ticker.title(),
                              type="nonfinancial", sector="IT", shares_outstanding=1.0))
        s.commit()


def test_upsert_fact_stamps_updated_at():
    Base.metadata.create_all(bind=engine)
    s = SessionLocal()
    _company(s, 99800, "TSTTS")
    s.query(models.FinancialFact).filter_by(company_id=99800).delete()
    s.commit()
    _upsert_fact(s, 99800, 2025, "revenue", 1000.0)
    s.commit()
    row = s.query(models.FinancialFact).filter_by(company_id=99800, concept="revenue").first()
    assert row.updated_at is not None
    s.close()


def test_upsert_hist_stamps_updated_at():
    Base.metadata.create_all(bind=engine)
    s = SessionLocal()
    _company(s, 99801, "TSTTS2")
    s.query(models.HistoricalFinancial).filter_by(company_id=99801).delete()
    s.commit()
    _upsert_hist(s, 99801, 2025, "PL", "revenue", 500.0)
    s.commit()
    row = s.query(models.HistoricalFinancial).filter_by(company_id=99801,
                                                        line_item="revenue").first()
    assert row.updated_at is not None
    s.close()


def test_alembic_chain_has_intg04_head():
    # intg04stmtts must sit in a SINGLE-head chain (guards a bad merge). The
    # exact head moves with each new migration — the current-head assertion
    # lives in the newest batch's test (test_batch24_inst.py).
    import os
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "alembic"))
    script = ScriptDirectory.from_config(cfg)
    assert len(list(script.get_heads())) == 1
    assert script.get_revision("intg04stmtts") is not None
