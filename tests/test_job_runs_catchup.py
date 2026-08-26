"""A scheduled job that never ran must stop being invisible.

17 Aug 2026: scheduler.py drives `schedule` with no persistence, so next_run is
recomputed from PROCESS START and a container recreated after a job's slot
silently drops that day's run. It cost the 14 Aug EOD session — 21 names against
1013 — and went unnoticed for three days. run_prices was fixed from the DATA
(app/eod_coverage.py); the other fourteen leave no trace, so app/job_runs.py
records the runs that did happen and infers the ones that did not.
"""
import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app import job_runs as jr


@pytest.fixture()
def db(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'jobs.db'}",
                        connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    try:
        yield s
    finally:
        s.close(); eng.dispose()


def _at(y, m, d, hh, mm):
    return dt.datetime(y, m, d, hh, mm, tzinfo=dt.timezone.utc)


# ── the ledger must not cry wolf ─────────────────────────────────────────────

def test_fresh_ledger_reports_nothing_overdue(db):
    """A freshly provisioned box has run nothing yet. Reporting all 15 jobs as
    missed on first boot is how an alert trains its reader to ignore it."""
    assert jr.overdue(db) == []


def test_slots_older_than_the_ledger_are_not_missed(db):
    """Absence of a record BEFORE the ledger existed is absence of evidence."""
    jr.record_run(db, "run_nse_flows", now=_at(2026, 8, 17, 2, 40))
    # A slot from the day before the ledger began must not count.
    out = [r["job"] for r in jr.overdue(db, now=_at(2026, 8, 17, 12, 0))]
    assert "run_regulatory_refresh" not in out


# ── detection ────────────────────────────────────────────────────────────────

def test_a_missed_run_is_detected(db):
    """The defect this exists for: the slot went by and nothing ran."""
    jr.record_run(db, "run_nse_flows", now=_at(2026, 8, 17, 2, 40))
    # Two days later, nothing further recorded: 17th's 02:30 is served, the
    # 18th's is not.
    out = {r["job"]: r for r in jr.overdue(db, now=_at(2026, 8, 18, 12, 0))}
    assert "run_nse_flows" in out
    assert out["run_nse_flows"]["last_run"].startswith("2026-08-17")
    assert out["run_nse_flows"]["missed_min"] > 0


def test_a_run_recorded_after_the_slot_clears_it(db):
    jr.record_run(db, "run_nse_flows", now=_at(2026, 8, 17, 2, 40))
    assert "run_nse_flows" not in {r["job"] for r in
                                   jr.overdue(db, now=_at(2026, 8, 17, 12, 0))}


# ── policy ───────────────────────────────────────────────────────────────────

def test_never_catch_up_jobs_are_never_replayed():
    """run_full and the results calendar are the quota-heavy pair. Replaying
    them would spend a large slice of the month's budget re-fetching what the
    next scheduled run fetches anyway — they are reported, not run. (This said
    "during the current vendor 429"; that Aug-2026 429 was the wrong vendor
    host, corrected 25 Aug 2026.)"""
    for job in ("run_full", "run_results_calendar"):
        rec = {"job": job, "policy": jr.JOBS[job][2], "due_at": _at(2026, 8, 16, 0, 30).isoformat()}
        assert rec["policy"] == "never_catch_up"
        assert jr.is_catchable(rec) is False


def test_catch_up_jobs_are_replayable():
    rec = {"job": "run_regulatory_refresh", "policy": "catch_up",
           "due_at": _at(2026, 8, 17, 2, 0).isoformat()}
    assert jr.is_catchable(rec) is True


def test_backup_is_only_catchable_inside_its_own_day():
    """backups/YYYY-MM-DD/ — a same-day replay overwrites its own prefix and
    recovers the day; a next-day replay just writes tomorrow's backup and
    recovers nothing, so it would be motion without repair."""
    slot = _at(2026, 8, 17, 4, 0)
    rec = {"job": "run_encrypted_backup", "policy": "catch_up", "due_at": slot.isoformat()}
    assert jr.is_catchable(rec, now=_at(2026, 8, 17, 9, 0)) is True
    assert jr.is_catchable(rec, now=_at(2026, 8, 18, 9, 0)) is False


def test_every_job_is_classified():
    """A job registered in scheduler.py but absent here would be silently
    unmonitored — the state this whole module exists to end."""
    assert len(jr.JOBS) == 15
    for name, (slot, cadence, policy, why) in jr.JOBS.items():
        assert policy in ("catch_up", "never_catch_up", "already_self_healing")
        assert cadence in (jr.DAILY, jr.WEEKDAYS, jr.SUN, jr.FRI)
        assert len(why) > 40, f"{name} needs a real reason, not a label"


# ── the brakes ───────────────────────────────────────────────────────────────

def test_catchup_cap_is_small_enough_to_matter():
    """After a long outage many jobs are overdue at once; starting them together
    on a 2 GB single-worker box is worse than the misses were."""
    assert 1 <= jr.MAX_PER_PASS <= 3


def test_claim_is_exclusive_then_expires(db):
    """A cutover can briefly run two schedulers; without a lease both start the
    same job."""
    assert jr.claim(db, "run_macro_refresh", now=_at(2026, 8, 17, 12, 0)) is True
    assert jr.claim(db, "run_macro_refresh", now=_at(2026, 8, 17, 12, 5)) is False
    later = _at(2026, 8, 17, 12, 0) + dt.timedelta(minutes=jr.LEASE_MIN + 1)
    assert jr.claim(db, "run_macro_refresh", now=later) is True


def test_a_completed_run_releases_the_lease(db):
    jr.claim(db, "run_macro_refresh", now=_at(2026, 8, 17, 12, 0))
    jr.record_run(db, "run_macro_refresh", now=_at(2026, 8, 17, 12, 10))
    assert jr.claim(db, "run_macro_refresh", now=_at(2026, 8, 17, 12, 11)) is True


def test_weekday_only_jobs_do_not_come_due_at_the_weekend():
    """run_manager_evidence is Mon-Fri; a Saturday must not report it missed."""
    sat = _at(2026, 8, 15, 23, 0)
    slot = jr.last_due_slot("run_manager_evidence", now=sat)
    assert slot is not None and slot.weekday() < 5


def test_ledger_arms_at_boot_so_the_gate_can_fire(db):
    """Without an armed ledger, overdue() skips every job on the `since is None`
    guard and jobs_overdue reads 0 for ever — a gate that cannot fire. Arming at
    boot means a slot passing AFTER the scheduler started is measurable even if
    no job has ever completed."""
    assert jr.overdue(db) == []                      # unarmed: nothing measurable
    jr.ensure_ledger(db, now=_at(2026, 8, 18, 1, 0))
    # 02:00 daily slot passes with nothing recorded -> now genuinely overdue
    out = {r["job"] for r in jr.overdue(db, now=_at(2026, 8, 18, 12, 0))}
    assert "run_regulatory_refresh" in out


def test_arming_is_idempotent_and_does_not_forgive_real_misses(db):
    """A restart must not slide the window forward and erase misses that
    happened before it."""
    jr.ensure_ledger(db, now=_at(2026, 8, 18, 1, 0))
    jr.ensure_ledger(db, now=_at(2026, 8, 19, 1, 0))   # later boot
    since = jr._state(db).get("_since")
    assert since.startswith("2026-08-18"), "the original start must survive"
