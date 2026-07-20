"""FIX-01 (ENG-01/FM-01/FM-08) — the nightly ledger append must survive a macro
failure. Before the fix, macro_regime NameError'd on an un-imported macro_data
and unwound snapshot_evidence before the ledger block, freezing the public
track record for 5 days. These pin the restructure: macro failure → flagged +
recorded, ledger still runs; and the published (never-backfilled) gap is
visible on the endpoint.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_fix01.db")

from app import manager_engine as me
from app.database import SessionLocal, engine, Base


def test_macro_failure_does_not_abort_the_ledger(monkeypatch):
    Base.metadata.create_all(bind=engine)
    # Evidence write succeeds with an empty eligible set (ledger loop trivial).
    monkeypatch.setattr(me, "build_evidence",
                        lambda db: {"names": {}, "weights": me.PRIOR_WEIGHTS})
    # The macro leg blows up — the exact ENG-01 scenario.
    def boom(db):
        raise RuntimeError("simulated macro outage")
    monkeypatch.setattr(me, "macro_regime", boom)
    monkeypatch.setattr(me, "load_macro", lambda db: {"regime": "neutral"})
    # Spy on the errors_1h feed (imported locally inside snapshot_evidence).
    import app.error_log as el
    recorded = {}
    monkeypatch.setattr(el, "record_error",
                        lambda db, path, exc: recorded.__setitem__("path", path))

    db = SessionLocal()
    try:
        out = me.snapshot_evidence(db)          # must NOT raise
    finally:
        db.close()

    assert out["macro_stale"] is True           # degraded state flagged
    assert "ledger_added" in out                # reached the return — no abort
    assert out["ledger_added"] == 0             # empty book → 0, but it RAN
    assert recorded.get("path") == "job:fm_macro_regime"   # errors_1h was fed


def test_ledger_endpoint_publishes_the_gap(monkeypatch):
    Base.metadata.create_all(bind=engine)
    from app.portfolio_routes import engine_ledger
    db = SessionLocal()
    try:
        out = engine_ledger(days=90, db=db)
    finally:
        db.close()
    assert out["gaps"] == me.LEDGER_GAPS
    assert out["gaps"][0]["from"] == "2026-07-16"
    assert "never" in out["note"].lower()       # doctrine intact


def test_gap_is_annotation_not_a_backfill():
    # Guardrail: the fix publishes a gap window, it does not fabricate rows.
    assert isinstance(me.LEDGER_GAPS, list) and len(me.LEDGER_GAPS) == 1
    g = me.LEDGER_GAPS[0]
    assert set(g) == {"from", "to", "reason"} and "backfill" in g["reason"].lower()
