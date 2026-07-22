"""Batch 30 — stored-vs-live intrinsic split (registered product-consistency
finding): the company page's canonical live result now writes back to the
stored Valuation row through the batch writer's own _payload mapping, so the
screener converges to exactly what the page showed (SAIL 38.07 vs 51.58)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")

from app.database import SessionLocal, engine, Base
from app import models
from app.main import _writeback_valuation


def _rec(intrinsic, verdict="HOLD", conf="medium"):
    return {
        "intrinsic": intrinsic, "mos": 0.10, "verdict": verdict,
        "composite": 55.0, "reliable": True,
        "confidence": {"level": conf}, "method": "Blended",
        "valuation_sector": "IT",
        "data_tier": "high", "method_dispersion": 1.2,
        "sensitivity_swing": 0.3, "tv_share": 0.5, "gate_state": "clean",
        "valuation": {"intrinsic": intrinsic},
        "fundamentals": {"roe": 0.22, "pb": 6.0, "pe": 24.0},
    }


def _co(db, tk):
    co = models.Company(ticker=tk, name=tk, type="nonfinancial",
                        sector="IT", shares_outstanding=100.0)
    db.add(co); db.commit()
    return co


def test_writeback_converges_stale_stored_row():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    co = _co(db, "SPLIT1")
    db.add(models.Valuation(company_id=co.id, intrinsic=38.07, mos=-0.2,
                            verdict="AVOID", confidence="low"))
    db.commit()
    _writeback_valuation(db, co, {"price": 45.0}, _rec(51.58, "HOLD", "medium"))
    row = db.query(models.Valuation).filter_by(company_id=co.id).first()
    assert abs(row.intrinsic - 51.58) < 1e-9        # screener now shows the page's number
    assert row.verdict == "HOLD" and row.confidence == "medium"
    db.close()


def test_writeback_creates_row_when_absent():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    co = _co(db, "SPLIT2")
    _writeback_valuation(db, co, {"price": 100.0}, _rec(120.0))
    row = db.query(models.Valuation).filter_by(company_id=co.id).first()
    assert row is not None and abs(row.intrinsic - 120.0) < 1e-9
    db.close()


def test_writeback_skips_when_converged():
    """Within 0.5% + same verdict/confidence → no write (cheap on hot names)."""
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    co = _co(db, "SPLIT3")
    db.add(models.Valuation(company_id=co.id, intrinsic=100.0, verdict="HOLD",
                            confidence="medium", mos=0.1))
    db.commit()
    _writeback_valuation(db, co, {"price": 90.0}, _rec(100.2))   # 0.2% off
    row = db.query(models.Valuation).filter_by(company_id=co.id).first()
    assert row.intrinsic == 100.0                   # untouched — guard skipped it
    db.close()


def test_writeback_failure_is_silent():
    """A broken rec (missing keys _payload needs) must never raise into the
    company page — the page's own response is the priority."""
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    co = _co(db, "SPLIT4")
    _writeback_valuation(db, co, {"price": 1.0}, {"intrinsic": 5.0})  # no valuation/fundamentals
    assert db.query(models.Valuation).filter_by(company_id=co.id).first() is None
    db.close()
