"""/api/health publishes the age of the last SUCCESSFUL encrypted backup.

WHY THIS EXISTS. In Sep 2026 the AWS account holding production was closed,
taking EC2, RDS and ECR with it. The only reason the platform survived was that
the nightly encrypted backup had been working — the database was rebuilt from
it. A backup that had been quietly failing for a few weeks would have been
terminal, and NOTHING would have said so:

  · run_backup() never raises; it returns {"status": "error"} and logs.
  · The scheduler records that into errors_1h — but a job that fails once a
    NIGHT is precisely the class uptime.yml documents a rate cannot catch: one
    error in one hourly bucket never reaches errors_1h>25 or
    error_hours_24h>=12.
  · status=="skipped" (BACKUP_KEY unset) is not an error at all, so backups
    could stop COMPLETELY while every signal on /api/health stayed green.

So this is an OUTCOME signal, the shape uptime.yml prescribes for exactly this
class ("price_age_days ... are the pattern"). It is stamped only on success, so
the age stops advancing whether the backup failed, was skipped, or the
scheduler is dead — one signal covering all three.

The threshold lives in .github/workflows/uptime.yml (>2 days); this file locks
the CONTRACT that alert reads.
"""
import datetime as dt
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ["DATABASE_URL"] = "sqlite:////tmp/_pytest_backup_freshness.db"

import pytest


@pytest.fixture(autouse=True)
def _clean_vendor_meter():
    """Zero the process-wide vendor meter: a prior file's failed vendor call
    would otherwise degrade health here and mask what these tests assert."""
    from app import vendor_meter
    importlib.reload(vendor_meter)
    yield


from app.database import Base, engine, SessionLocal   # noqa: E402
from app import models                                 # noqa: E402
from app.main import health                            # noqa: E402


@pytest.fixture()
def db():
    Base.metadata.create_all(engine)
    s = SessionLocal()
    yield s
    s.close()
    Base.metadata.drop_all(engine)


def _stamp(db, date_str):
    db.add(models.KVStore(key="last_backup", value={"date": date_str, "tables": 26}))
    db.commit()


def test_field_is_present_for_the_uptime_alert(db):
    assert "backup_age_days" in health(db=db)


def test_none_until_a_backup_has_succeeded(db):
    """Unmeasured is not bad: a fresh database has never backed up, and
    uptime.yml reads null as 'not deployed yet' rather than paging."""
    assert health(db=db)["backup_age_days"] is None


def test_todays_backup_reads_zero(db):
    _stamp(db, dt.date.today().isoformat())
    assert health(db=db)["backup_age_days"] == 0


def test_a_stale_backup_is_visible(db):
    """The whole point: backups that stopped days ago must SHOW. 9 days is what
    the Sep-2026 outage would have looked like had the job been failing."""
    _stamp(db, (dt.date.today() - dt.timedelta(days=9)).isoformat())
    assert health(db=db)["backup_age_days"] == 9


def test_a_skipped_backup_still_ages(db):
    """status=="skipped" (BACKUP_KEY unset) records no error and no new stamp,
    so the age keeps growing off the last SUCCESS — the case that would
    otherwise be completely silent."""
    _stamp(db, (dt.date.today() - dt.timedelta(days=3)).isoformat())
    # a skipped run writes nothing; the age must reflect the last real backup
    assert health(db=db)["backup_age_days"] == 3


def test_signal_does_not_degrade_health_by_itself(db):
    """Reported, not degraded — the threshold belongs to uptime.yml, which knows
    it is probing production. A dev box with an old backup is not 'degraded'."""
    _stamp(db, (dt.date.today() - dt.timedelta(days=30)).isoformat())
    assert health(db=db)["status"] == "ok"


def test_unparseable_date_is_unmeasured_not_a_crash(db):
    """A corrupt KV value must read as 'not measured', never take health down —
    health is the endpoint uptime depends on to tell it anything at all."""
    db.add(models.KVStore(key="last_backup", value={"date": "not-a-date"}))
    db.commit()
    out = health(db=db)
    assert out["backup_age_days"] is None


# ── The WRITER ───────────────────────────────────────────────────────────────
# Everything above drives the reader with a hand-inserted row, so all of it
# passed while the thing that WRITES that row was broken. The first version of
# the stamp was inline in scheduler.run_encrypted_backup() and used `_dt`
# without importing it there: every nightly backup succeeded, the stamp raised
# NameError into its own except, and backup_age_days would have stayed null
# forever. Caught only by reading production logs. These pin the writer.

def test_stamp_writes_the_row_health_reads(db):
    """End-to-end in one test: stamp a successful run, then assert health sees
    it. A reader-only test cannot fail when the writer is broken."""
    from app.backup import stamp_last_backup
    ok = stamp_last_backup(db, {"status": "ok", "date": dt.date.today().isoformat(),
                                "tables": 26, "bytes_enc": 51342896})
    assert ok is True
    assert health(db=db)["backup_age_days"] == 0


def test_stamp_updates_rather_than_duplicating(db):
    """Second night must overwrite, not accumulate a second KVStore row."""
    from app.backup import stamp_last_backup
    stamp_last_backup(db, {"status": "ok", "date": "2026-09-01", "tables": 26})
    stamp_last_backup(db, {"status": "ok", "date": dt.date.today().isoformat(), "tables": 26})
    rows = db.query(models.KVStore).filter_by(key="last_backup").all()
    assert len(rows) == 1
    assert health(db=db)["backup_age_days"] == 0


@pytest.mark.parametrize("status", ["error", "skipped", None])
def test_a_run_that_did_not_succeed_is_never_stamped(db, status):
    """The age must advance off the last SUCCESS only — stamping a failed or
    skipped run would make a dead backup look fresh, which is worse than having
    no signal at all."""
    from app.backup import stamp_last_backup
    assert stamp_last_backup(db, {"status": status, "date": dt.date.today().isoformat()}) is False
    assert health(db=db)["backup_age_days"] is None


def test_stamp_never_raises_even_on_a_broken_session(db):
    """A good backup must not read as bad because bookkeeping failed. This is
    also the guard that the NameError bug hid behind — so assert the contract
    holds while ALSO asserting (above) that the happy path really writes."""
    from app.backup import stamp_last_backup

    class _Broken:
        def query(self, *a, **k): raise RuntimeError("db gone")
    assert stamp_last_backup(_Broken(), {"status": "ok", "date": "2026-09-08"}) is False
