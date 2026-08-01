"""Same-day snapshot divergence — a stale VALUE wearing a FRESH timestamp.

Found live 2026-08-01: IndianAPI's 31-Jul EOD run stamped as_of=31-Jul on
BAJFINANCE at 1054.4 (exactly 29 July's close) while 31 July closed at 1141.2.
The date check saw a current date; the 30% catastrophic bar saw only 7.6%. The
screener published a 7.6%-wrong price.
"""
import os, sys, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from app.database import Base, engine, SessionLocal
from app import models
from app.dhan.backfill import sync_snapshots_from_history, SAME_DAY_TOL


@pytest.fixture(autouse=True)
def _db():
    Base.metadata.drop_all(engine); Base.metadata.create_all(engine)
    yield


def _mk(db, ticker, snap_price, snap_as_of, bar_date, bar_close):
    co = models.Company(ticker=ticker, name=ticker, type="nonfinancial",
                        sector="X", shares_outstanding=100.0)
    db.add(co); db.flush()
    db.add(models.MarketSnapshot(company_id=co.id, price=snap_price, as_of=snap_as_of))
    db.add(models.HistoricalPrice(company_id=co.id, date=bar_date, open=bar_close,
                                  high=bar_close, low=bar_close, close=bar_close,
                                  volume=1000.0))
    db.commit()
    return co


def test_stale_value_with_fresh_timestamp_is_corrected():
    """The live BAJFINANCE case: same date, post-close, 7.6% gap -> Dhan wins."""
    db = SessionLocal()
    co = _mk(db, "BAJFINANCE", 1054.4, dt.datetime(2026, 7, 31, 10, 15),
             dt.date(2026, 7, 31), 1141.2)
    out = sync_snapshots_from_history(db, ["BAJFINANCE"])
    m = db.query(models.MarketSnapshot).filter_by(company_id=co.id).first()
    assert out["corrected"] == 1
    assert m.price == pytest.approx(1141.2)
    db.close()


def test_intraday_snapshot_is_NOT_overwritten_by_a_full_day_bar():
    """Pre-close mark vs a full-day bar differs honestly — must be left alone."""
    db = SessionLocal()
    co = _mk(db, "FOO", 1054.4, dt.datetime(2026, 7, 31, 6, 0),   # 11:30 IST
             dt.date(2026, 7, 31), 1141.2)
    out = sync_snapshots_from_history(db, ["FOO"])
    m = db.query(models.MarketSnapshot).filter_by(company_id=co.id).first()
    assert out["corrected"] == 0
    assert m.price == pytest.approx(1054.4), "an intraday mark must survive"
    db.close()


def test_small_same_day_gap_is_tolerated():
    """Dividend/adjustment noise under the tolerance must not churn the row."""
    db = SessionLocal()
    close = 1000.0
    snap = close * (1 - SAME_DAY_TOL / 2)
    co = _mk(db, "BAR", snap, dt.datetime(2026, 7, 31, 10, 15),
             dt.date(2026, 7, 31), close)
    out = sync_snapshots_from_history(db, ["BAR"])
    m = db.query(models.MarketSnapshot).filter_by(company_id=co.id).first()
    assert out["corrected"] == 0
    assert m.price == pytest.approx(snap)
    db.close()


def test_older_snapshot_still_marked_forward():
    """The original date-based path must keep working."""
    db = SessionLocal()
    co = _mk(db, "BAZ", 900.0, dt.datetime(2026, 7, 29, 10, 0),
             dt.date(2026, 7, 31), 1000.0)
    out = sync_snapshots_from_history(db, ["BAZ"])
    m = db.query(models.MarketSnapshot).filter_by(company_id=co.id).first()
    assert out["updated"] == 1
    assert m.price == pytest.approx(1000.0)
    db.close()


def test_catastrophic_gap_still_wins_on_a_different_day():
    """The >30% mis-resolution guard must survive the new same-day branch."""
    db = SessionLocal()
    co = _mk(db, "QUX", 1020.0, dt.datetime(2026, 7, 31, 6, 0),   # intraday
             dt.date(2026, 7, 30), 10156.0)                        # 10x gap
    out = sync_snapshots_from_history(db, ["QUX"])
    m = db.query(models.MarketSnapshot).filter_by(company_id=co.id).first()
    assert out["corrected"] == 1
    assert m.price == pytest.approx(10156.0)
    db.close()
