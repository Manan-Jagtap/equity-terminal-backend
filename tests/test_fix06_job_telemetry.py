"""FIX-06/ARCH-03 — a swallowed scheduled-job failure reaches errors_1h.

Scheduler jobs catch their own exceptions and only `log.error` them, so a failing
nightly job never showed up in /api/health and paged nobody (the silence behind
the 5-day ledger freeze). `record_job_error` funnels the caught exception into
error_log → errors_1h → the FIX-05 uptime alert. Tested against the importable
helper (scheduler.py runs a while-loop at import).

Reads use a FRESH session — exactly like /api/health does — because the helper
commits from its own session (a scheduler job has no request-scoped db)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ["DATABASE_URL"] = "sqlite:////tmp/_pytest_fix06.db"

import pytest

from app import models  # noqa: F401 — register KVStore on Base before create_all
from app.database import Base, engine, SessionLocal
from app.job_telemetry import record_job_error
from app.error_log import errors_last_hour, recent_errors


@pytest.fixture()
def db():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


def _fresh():
    return SessionLocal()


def test_induced_job_failure_is_visible_in_errors_1h(db):
    def snapshot_verdicts():            # stands in for a real scheduler job
        try:
            raise ValueError("boom")
        except Exception as e:
            record_job_error(e)         # exactly what the wired handlers now call
    snapshot_verdicts()
    s = _fresh()
    try:
        assert errors_last_hour(s) >= 1     # the signal /api/health surfaces
        rec = recent_errors(s)
    finally:
        s.close()
    assert any(r["path"] == "job:snapshot_verdicts" for r in rec)   # auto-derived tag
    assert any(r["type"] == "ValueError" and r["msg"] == "boom" for r in rec)


def test_explicit_tag(db):
    record_job_error(RuntimeError("x"), tag="job:run_compute")
    s = _fresh()
    try:
        assert any(r["path"] == "job:run_compute" for r in recent_errors(s))
    finally:
        s.close()


def test_never_raises_even_on_bad_input(db):
    # telemetry must never escalate a job failure into a crash
    record_job_error(None)
    record_job_error(ValueError("ok"), tag=None)
