"""FIX-07/DATA-04 — every IndianAPI call is metered into the monthly budget.

Before this, only the ingester's primary /stock GET was counted; its ~9 insight
calls per name and every on-demand route were invisible, so the budget guard saw
~10-15% of real spend. vendor_meter is the one counter every vendor GET ticks;
it flushes to the durable ApiUsage tally at request/heartbeat boundaries."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ["DATABASE_URL"] = "sqlite:////tmp/_pytest_fix07.db"

import pytest
from app import models  # noqa: F401 — register ApiUsage before create_all
from app.database import Base, engine, SessionLocal
from app import vendor_meter, api_budget


@pytest.fixture()
def db():
    Base.metadata.create_all(engine)
    vendor_meter.drain()          # clear any cross-test residue
    s = SessionLocal()
    yield s
    s.close()
    Base.metadata.drop_all(engine)


def test_tick_accumulates_and_drain_resets(db):
    vendor_meter.drain()
    for _ in range(5):
        vendor_meter.tick()
    assert vendor_meter.pending() == 5
    assert vendor_meter.drain() == 5
    assert vendor_meter.pending() == 0


def test_flush_persists_to_monthly_budget(db):
    vendor_meter.drain()
    vendor_meter.tick(); vendor_meter.tick(); vendor_meter.tick()
    assert vendor_meter.flush(db) == 3
    assert api_budget.month_usage(db) == 3
    # a second batch adds to the same monthly tally
    for _ in range(10):
        vendor_meter.tick()
    vendor_meter.flush(db)
    assert api_budget.month_usage(db) == 13


def test_flush_is_noop_at_zero(db):
    vendor_meter.drain()
    assert vendor_meter.flush(db) == 0
    assert api_budget.month_usage(db) == 0


def test_ingester_get_safe_now_ticks_the_meter(db, monkeypatch):
    # the historic blind spot: _get_safe made a vendor call but never counted.
    from app.ingest import indianapi_ingester as ing
    vendor_meter.drain()

    class _Resp:
        status_code = 200
        def json(self): return {"ok": 1}
    monkeypatch.setattr(ing, "KEY", "realkey")
    monkeypatch.setattr(ing.requests, "get", lambda *a, **k: _Resp())
    ing._get_safe("/ratios", {"stock_name": "TCS"})
    assert vendor_meter.pending() >= 1
