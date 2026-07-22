"""Batch 19 — FIX-27 scale headroom (SCALE-01/02/03, OPS-05/08, SCALE-05).
Behaviour-critical bits: the DB-side `_all_latest_facts` rewrite must return the
same latest-per-concept map as the old full-table Python load, and /api/health
must read the EOD date from the heartbeat KV (O(1)) rather than scanning prices."""
import datetime as dt
from fastapi.testclient import TestClient
from app.database import SessionLocal, engine, Base
from app import models, main
from app.main import app


def _company(db, cid, ticker):
    if not db.query(models.Company).filter_by(id=cid).first():
        db.add(models.Company(id=cid, ticker=ticker, name=ticker.title(),
                              type="nonfinancial", sector="IT", shares_outstanding=1.0))
        db.commit()


def test_all_latest_facts_returns_latest_per_concept():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    _company(db, 99700, "TSTFACT")
    db.query(models.FinancialFact).filter_by(company_id=99700).delete()
    db.commit()
    # two concepts, two years — expect the LATEST year's value for each
    for yr, rev, pat in [(2023, 100.0, 10.0), (2024, 200.0, 25.0)]:
        db.add(models.FinancialFact(company_id=99700, concept="revenue", fiscal_year=yr, value=rev))
        db.add(models.FinancialFact(company_id=99700, concept="pat", fiscal_year=yr, value=pat))
    db.commit()
    out = main._all_latest_facts(db)
    db.close()
    assert out[99700]["revenue"] == 200.0
    assert out[99700]["pat"] == 25.0


def test_health_reads_eod_date_from_heartbeat_kv():
    # SCALE-03: health must derive price_age_days from the heartbeat's
    # latest_eod_date (O(1)), not by scanning HistoricalPrice.
    db = SessionLocal()
    d = (dt.date.today() - dt.timedelta(days=2)).isoformat()
    val = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "latest_eod_date": d}
    row = db.query(models.KVStore).filter_by(key="scheduler_heartbeat").first()
    if row:
        row.value = val
    else:
        db.add(models.KVStore(key="scheduler_heartbeat", value=val))
    db.commit()
    db.close()
    r = TestClient(app).get("/api/health")
    assert r.status_code == 200
    assert r.json()["price_age_days"] == 2      # from the KV, no price scan needed


def test_pool_engine_imports_cleanly():
    # SQLite must NOT receive the Postgres pool kwargs (they'd error at engine
    # creation); a non-SQLite URL must get the sized pool. A clean import proves
    # the dialect guard holds under whichever DATABASE_URL the CI job uses.
    import app.database as d
    assert d.engine is not None
    if not d.DATABASE_URL.startswith("sqlite"):
        assert d.engine.pool.size() == 10        # SCALE-01 pool sizing applied
