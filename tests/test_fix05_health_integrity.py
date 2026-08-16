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
    for k in ("errors_1h", "error_hours_24h", "scheduler_beat_min",
              "price_age_days", "integrity", "integrity_age_days"):
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


def test_health_carries_error_hours_from_the_ring(db):
    """The call site, not just the helper: a ring with one error in each of 13
    distinct hours reads errors_1h=1 (invisible to the storm rule) and
    error_hours_24h=13 (past uptime.yml's `> 11`) THROUGH health()."""
    import datetime as dt
    from app.manager_engine import _kv_put
    from app.error_log import KEY as ERR_KEY
    now = dt.datetime.now(dt.timezone.utc)
    _kv_put(db, ERR_KEY, [{"ts": (now - dt.timedelta(minutes=60 * h + 20)).isoformat(timespec="seconds"),
                           "path": "job:run_intraday_prices", "type": "HTTPError", "msg": "401"}
                          for h in range(13)])
    out = health(db=db)
    assert out["errors_1h"] == 1 and out["error_hours_24h"] == 13
    assert out["status"] == "ok"        # a signal, not a degrade: the threshold lives in uptime.yml


# ── Sweep freshness: `integrity` is a verdict with no date ───────────────────

def test_integrity_age_is_none_without_a_sweep_or_without_as_of(db):
    """Same null contract as `integrity`: no sweep → None; and a stored blob
    with no parseable `as_of` (the hand-written shape above) → None too, WITHOUT
    dragging the verdict into "unmeasured" — not knowing the age is not an
    outage. uptime.yml keeps both OUT of its null gate for exactly this reason."""
    from app.manager_engine import _kv_put
    from app.data_integrity import KEY
    assert health(db=db)["integrity_age_days"] is None
    _kv_put(db, KEY, {"status": "green", "n_findings": 0})
    out = health(db=db)
    assert out["integrity"] == "green" and out["integrity_age_days"] is None
    assert out["status"] == "ok"
    _kv_put(db, KEY, {"status": "green", "as_of": "not-a-date"})
    out = health(db=db)
    assert out["integrity"] == "green" and out["integrity_age_days"] is None
    assert out["status"] == "ok"


def test_integrity_age_is_whole_days_since_the_sweeps_as_of(db):
    """The gap this closes: a green sweep from weeks ago stayed green forever.
    Age is whole days since the sweep's own UTC `as_of` — the field
    run_integrity_sweep stamps — so uptime.yml can gate `> 7` (weekly cadence:
    0-6 normal, 7 the due Sunday, 8+ a missed slot)."""
    import datetime as dt
    from app.manager_engine import _kv_put
    from app.data_integrity import KEY, run_integrity_sweep
    now = dt.datetime.now(dt.timezone.utc)
    stale = (now - dt.timedelta(days=23, hours=5)).isoformat(timespec="seconds")
    _kv_put(db, KEY, {"status": "green", "as_of": stale})
    out = health(db=db)
    assert out["integrity"] == "green" and out["integrity_age_days"] == 23
    # A 'Z' suffix parses too (the heartbeat's convention, should a writer use it).
    _kv_put(db, KEY, {"status": "green", "as_of": stale.replace("+00:00", "Z")})
    assert health(db=db)["integrity_age_days"] == 23
    # A sweep stored just now — through the REAL sweep, so the `as_of` this
    # reads is the one store_sweep actually writes — is 0 days old.
    _kv_put(db, KEY, run_integrity_sweep(db))
    assert health(db=db)["integrity_age_days"] == 0
