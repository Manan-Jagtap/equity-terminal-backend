"""FIX-05 / OPS-01/OPS-06 — /api/health exposes the data-integrity sweep verdict.

The uptime workflow has no admin token, so the weekly integrity sweep (red/amber/
green, stored in KVStore) was invisible to alerting. Health now surfaces a bare
`integrity` status the token-less uptime.yml can page on. The scheduler_beat_min
and price_age_days threshold alerts live in .github/workflows/uptime.yml (shell,
exercised there); here we lock the health CONTRACT those alerts read."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ["DATABASE_URL"] = "sqlite:////tmp/_pytest_fix05.db"

import pytest

from app.database import Base, engine, SessionLocal
from app import models
from app.main import health


@pytest.fixture()
def db():
    Base.metadata.create_all(engine)
    s = SessionLocal()
    yield s
    s.close()
    Base.metadata.drop_all(engine)


def test_health_carries_the_fields_uptime_alerts_on(db):
    out = health(db=db)
    assert out["status"] == "ok"
    # the exact keys uptime.yml reads via jq — the alerting contract
    for k in ("errors_1h", "scheduler_beat_min", "price_age_days", "integrity"):
        assert k in out


def test_integrity_is_none_until_a_sweep_is_stored(db):
    # no sweep in KV yet → None; uptime.yml treats missing as green (no alarm)
    assert health(db=db)["integrity"] is None


def test_integrity_reflects_the_stored_sweep_status(db):
    from app.manager_engine import _kv_put
    from app.data_integrity import KEY
    _kv_put(db, KEY, {"status": "red", "n_findings": 1})
    assert health(db=db)["integrity"] == "red"
    _kv_put(db, KEY, {"status": "green", "n_findings": 0})
    assert health(db=db)["integrity"] == "green"
