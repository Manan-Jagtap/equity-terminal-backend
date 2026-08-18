"""SEC-12: usage-telemetry retention is now enforced by a standing daily
scheduler job, not only when an admin opens the usage page. This locks the
prune's cutoff behaviour (DPDP data-minimisation) — old rows go, fresh stay."""
import os, sys, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")


def test_prune_removes_only_expired_usage_events():
    from app.database import SessionLocal, engine, Base
    from app import models  # noqa: F401
    from app.telemetry_routes import prune_usage_events, RETENTION_DAYS
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    now = dt.datetime.utcnow()
    old = models.UsageEvent(event="view:old",
                            created_at=now - dt.timedelta(days=RETENTION_DAYS + 5))
    fresh = models.UsageEvent(event="view:fresh",
                              created_at=now - dt.timedelta(days=1))
    db.add_all([old, fresh])
    db.commit()
    n = prune_usage_events(db)
    assert n >= 1
    remaining = {e.event for e in db.query(models.UsageEvent).all()}
    assert "view:fresh" in remaining          # inside the window — kept
    assert "view:old" not in remaining        # past retention — pruned
    db.close()


def test_scheduler_registers_daily_usage_prune():
    """The prune must be wired into the scheduler, not left to admin visits.
    Read the source as text (no import) — importing scheduler registers jobs
    and pulls the full app graph, which this assertion doesn't need."""
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scheduler.py")
    src = open(path, encoding="utf-8").read()
    assert "def run_usage_prune" in src
    # The registration is now wrapped: `.do(_tracked(run_usage_prune))`. _tracked
    # stamps a COMPLETED run into app/job_runs.py, which is how a slot that went
    # by unserved becomes visible at all — `schedule` has no memory across
    # processes, so a container recreated after 03:15 silently drops that day's
    # prune and nothing would otherwise record it. Assert the slot and the job
    # separately so this guard survives the next wrapper without going quiet.
    assert 'schedule.every().day.at("03:15")' in src
    assert 'do(_tracked(run_usage_prune))' in src
