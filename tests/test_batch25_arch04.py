"""Batch 25 — FIX-28 tail (part 5): ARCH-04 typed KV envelope + registry.
Additive by design: values keep their exact shape (existing readers untouched);
kv_put stamps a kv_meta:<key> sidecar so staleness is finally queryable."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")

from fastapi.testclient import TestClient
from app.database import SessionLocal, engine, Base
from app import models
from app.main import app
from app.kv import kv_put, kv_get, kv_meta, kv_health, owner_of

client = TestClient(app)


def _db():
    Base.metadata.create_all(bind=engine)
    return SessionLocal()


def test_kv_put_keeps_value_shape_and_stamps_meta():
    db = _db()
    kv_put(db, "arch04_test_blob", {"a": 1}, schema_version=3)
    # the value row is EXACTLY what was written — no envelope wrapping
    assert kv_get(db, "arch04_test_blob") == {"a": 1}
    raw = db.query(models.KVStore).filter_by(key="arch04_test_blob").first()
    assert raw.value == {"a": 1}
    m = kv_meta(db, "arch04_test_blob")
    assert m["schema_version"] == 3 and m["updated_at"]
    db.close()


def test_meta_none_for_pre_envelope_keys():
    db = _db()
    db.add(models.KVStore(key="arch04_legacy_blob", value={"old": True}))
    db.commit()
    assert kv_meta(db, "arch04_legacy_blob") is None    # predates envelope: honest None
    db.close()


def test_registry_owner_exact_and_prefix():
    assert owner_of("fm_evidence_v1") == "app/manager_engine.py"
    assert owner_of("fm_cash_u42") == "app/portfolio_routes.py"      # prefix
    assert owner_of("pending_signup:x@y.z") == "app/auth_routes.py"  # prefix
    assert owner_of("some_unregistered_key") is None


def test_kv_health_lists_keys_with_age():
    db = _db()
    kv_put(db, "arch04_health_blob", {"x": 1})
    rows = kv_health(db)
    mine = [r for r in rows if r["key"] == "arch04_health_blob"]
    assert mine and mine[0]["age_hours"] is not None and mine[0]["age_hours"] < 1
    # meta sidecars themselves never appear as keys
    assert not any(r["key"].startswith("kv_meta:") for r in rows)
    # legacy (pre-envelope) rows appear with null age, not an error
    legacy = [r for r in rows if r["key"] == "arch04_legacy_blob"]
    if legacy:
        assert legacy[0]["updated_at"] is None
    db.close()


def test_manager_engine_kv_put_delegates_to_envelope():
    """The FM blobs are the ENG-01 staleness surface — their writer must stamp."""
    from app.manager_engine import _kv_put
    db = _db()
    _kv_put(db, "arch04_fm_probe", {"ev": []})
    assert kv_meta(db, "arch04_fm_probe") is not None
    assert kv_get(db, "arch04_fm_probe") == {"ev": []}
    db.close()


def test_admin_kv_health_requires_admin():
    assert client.get("/api/admin/kv-health").status_code in (401, 403)
