"""Batch 24 — FIX-28 tail (part 4): INST-01/02 self-owned analytics + feedback.
DPDP-fit by construction: our DB only, no IP/UA, event names sanitised, query
strings stripped, 180-day retention, and account erasure anonymises usage rows
while deleting user-authored feedback."""
import datetime as dt
from fastapi.testclient import TestClient
from app.database import SessionLocal, engine, Base
from app import models
from app.main import app
from app.telemetry_routes import _clean_event, _clean_detail, prune_usage_events

client = TestClient(app)


# ── sanitisation (data-minimal by construction) ──────────────────────────────

def test_event_name_sanitised():
    assert _clean_event("View:Screener") == "view:screener"
    assert _clean_event("evil<script>!!") == "evilscript"
    assert len(_clean_event("x" * 500)) == 64


def test_detail_strips_query_strings():
    assert _clean_detail("/company/TCS?token=SECRET&x=1") == "/company/TCS"
    assert _clean_detail(None) is None


# ── endpoints ────────────────────────────────────────────────────────────────

def test_anonymous_telemetry_recorded():
    Base.metadata.create_all(bind=engine)
    r = client.post("/api/telemetry", json={"event": "view:screener",
                                            "detail": "/screener?q=abc"})
    assert r.status_code == 200 and r.json()["ok"] is True
    db = SessionLocal()
    row = (db.query(models.UsageEvent)
             .order_by(models.UsageEvent.id.desc()).first())
    assert row.event == "view:screener"
    assert row.detail == "/screener"           # query string never stored
    assert row.user_id is None                 # anonymous OK
    db.close()


def test_feedback_requires_auth():
    r = client.post("/api/feedback", json={"message": "the screener is great"})
    assert r.status_code == 401                # anonymous free-text = spam sink


def test_admin_usage_and_feedback_require_admin():
    assert client.get("/api/admin/usage").status_code in (401, 403)
    assert client.get("/api/admin/feedback").status_code in (401, 403)


# ── retention + erasure semantics ────────────────────────────────────────────

def test_retention_prunes_old_events():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    old = models.UsageEvent(user_id=None, event="view:old")
    db.add(old)
    db.commit()
    # backdate past the retention window
    old.created_at = dt.datetime.utcnow() - dt.timedelta(days=200)
    db.commit()
    n = prune_usage_events(db, days=180)
    assert n >= 1
    assert not (db.query(models.UsageEvent).filter_by(event="view:old").first())
    db.close()


def test_erasure_semantics_anonymise_usage_delete_feedback():
    """Mirrors the delete_account wiring: usage rows lose their user link
    (aggregates survive), feedback rows are removed outright."""
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.add(models.UsageEvent(user_id=424242, event="view:x"))
    db.add(models.Feedback(user_id=424242, message="my personal note"))
    db.commit()
    # the exact statements delete_account runs
    db.query(models.Feedback).filter_by(user_id=424242).delete(synchronize_session=False)
    db.query(models.UsageEvent).filter_by(user_id=424242).update(
        {models.UsageEvent.user_id: None}, synchronize_session=False)
    db.commit()
    assert db.query(models.Feedback).filter_by(user_id=424242).count() == 0
    ev = db.query(models.UsageEvent).filter_by(event="view:x").order_by(
        models.UsageEvent.id.desc()).first()
    assert ev is not None and ev.user_id is None
    db.close()


def test_alembic_chain_has_inst0102():
    # inst0102tel must sit in a SINGLE-head chain (guards a bad merge). The
    # exact head moves with each new migration — the current-head assertion
    # lives in the newest migration's test (test_arc04_orphan_columns.py).
    import os
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "alembic"))
    script = ScriptDirectory.from_config(cfg)
    assert len(list(script.get_heads())) == 1
    assert script.get_revision("inst0102tel") is not None
