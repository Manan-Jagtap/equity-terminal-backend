"""Batch 27 — FIX-28 tail (part 7): DATA-09 intraday-fallback metering flush
+ runbook documentation of the ~53-name failover blast radius."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")


def test_fallback_burst_flushes_vendor_meter():
    """The IndianAPI-fallback branch must flush pending ticks durably right
    after the burst (not wait for the next heartbeat — a process death in
    between would lose ~53 metered calls)."""
    import re
    src = open(os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            "scheduler.py")).read()
    # the flush must live INSIDE the fallback branch (after run_intraday)
    m = re.search(r"n = run_intraday\(\)(.{0,700})", src, re.S)
    assert m, "IndianAPI fallback branch not found"
    assert "vendor_meter" in m.group(1) and "flush" in m.group(1)


def test_vendor_meter_flush_persists_ticks():
    """End-to-end: tick → flush → durable ApiUsage tally grows by the burst."""
    from app.database import SessionLocal, engine, Base
    from app import vendor_meter, api_budget
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    before = api_budget.month_usage(db)
    vendor_meter.tick(53)                      # a full fallback burst
    flushed = vendor_meter.flush(db)
    assert flushed >= 53
    assert api_budget.month_usage(db) >= before + 53
    assert vendor_meter.pending() == 0
    db.close()


def test_runbook_documents_blast_radius():
    """ARCHITECTURE.md must state the failover scope trade-off explicitly."""
    doc = open(os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            "ARCHITECTURE.md")).read()
    assert "DATA-09" in doc
    assert "blast radius" in doc.lower()
    assert "UNIVERSE" in doc
