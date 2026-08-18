"""
app/job_runs.py — did every scheduled job actually run, and what do we do when
one didn't?

17 Aug 2026. scheduler.py drives the `schedule` library with
`while True: run_pending()` and no persistence, so each job's next_run is
recomputed from PROCESS START. A container recreated after a job's slot — i.e.
every deploy past that time of day — silently drops that day's run. Nothing
retried it and nothing recorded that it had not happened.

run_prices was fixed first, in app/eod_coverage.py, because the DATA answers the
question: if the last completed session is missing from historical_prices, the
job did not run, whatever the reason. The other fourteen jobs have no such tell —
a regulatory refresh that never fired leaves the database looking exactly like
one that fired and found nothing new. So they need the run itself recorded.

WHAT THIS IS NOT. It is not a second scheduler. `schedule` still owns the timing;
this only notices that a slot went by unserved and decides what to do about it,
which is a different question per job:

  catch_up            replaying it recovers something real, and replaying it
                      twice is harmless.
  never_catch_up      the miss is recorded and surfaced, but NOT replayed —
                      either because the replay cannot recover what was lost, or
                      because it would spend vendor quota chasing data the
                      vendor will not return (IndianAPI is at 429 today, and
                      api_budget already refuses).
  already_self_healing  the next ordinary run subsumes the missed one, so
                      catching up would be pure duplicate work.

The classification lives in JOBS below, one line per job with its reason. It was
derived by reading each job BODY — not by grepping for `delete`, which gets
run_usage_prune exactly wrong (see its note).
"""
from __future__ import annotations

import datetime as dt

from . import models

KEY = "job_runs_v1"

# How far past a slot a job must be before it counts as missed. Long enough that
# a job which is simply SLOW (run_full walks the universe) is never called
# overdue while it is still working.
GRACE_MIN = 90

# Most a single catch-up pass will start. After a long outage many jobs are
# overdue at once, and booting into run_data_integrity + run_encrypted_backup +
# a macro refresh together on a 2 GB single-worker box is worse than the misses
# were. The pass runs again on its interval, so a backlog drains steadily
# instead of arriving all at once.
MAX_PER_PASS = 2

# Another instance's in-flight catch-up is respected this long. A cutover can
# briefly run two schedulers; without this both would start the same job.
LEASE_MIN = 30

DAILY, WEEKDAYS, SUN, FRI = "daily", "weekdays", "sunday", "friday"

# name -> (slot HH:MM UTC, cadence, policy, why)
JOBS: dict[str, tuple[str, str, str, str]] = {
    "run_prices": ("10:15", WEEKDAYS, "already_self_healing",
        "app/eod_coverage.py already repairs this from the data itself."),
    "run_coverage_backfill": ("20:30", DAILY, "already_self_healing",
        "Works from coverage.needs_fundamentals each run, so a missed day is "
        "simply included in the next one's worklist."),
    "run_missing_history_backfill": ("10:45", WEEKDAYS, "already_self_healing",
        "Seeds names that lack a price series; the gap persists until filled, "
        "so the next run finds the same names."),
    "run_transcript_ingest": ("01:00", DAILY, "already_self_healing",
        "Ingests transcripts not yet stored — a missed day leaves them "
        "outstanding and the next run picks them up."),
    "run_usage_prune": ("03:15", DAILY, "already_self_healing",
        "The delete is a ROLLING cutoff (created_at < now - RETENTION_DAYS), "
        "not a per-day partition, so a missed day is swept by the next run "
        "with an advanced cutoff. Counting its `delete` calls suggests danger; "
        "reading it shows the opposite."),

    "run_regulatory_refresh": ("02:00", DAILY, "catch_up",
        "Refreshes stored regulatory data; replay is a no-op when nothing "
        "changed and spends no vendor quota."),
    "run_nse_flows": ("02:30", DAILY, "catch_up",
        "FII/DII flow rows are per-day and a missed day stays missing."),
    "run_data_integrity": ("03:00", SUN, "catch_up",
        "The weekly sweep writes a verdict health alerts on; without it the "
        "stored verdict silently ages."),
    "run_encrypted_backup": ("04:00", DAILY, "catch_up",
        "Writes backups/YYYY-MM-DD/, so a SAME-DAY replay overwrites its own "
        "prefix and is idempotent. Deliberately not carried past that day: a "
        "next-day run writes tomorrow's backup and cannot reconstruct the "
        "missed one — see is_catchable()."),
    "run_manager_evidence": ("11:15", WEEKDAYS, "catch_up",
        "Rebuilds evidence from stored data; no vendor spend."),
    "run_macro_refresh": ("23:30", SUN, "catch_up",
        "Public macro sources, no IndianAPI quota."),
    "_monthly_manager_calibration": ("21:00", FRI, "catch_up",
        "Guarded internally to the first week of the month; replay inside that "
        "window recomputes the same calibration."),
    "_monthly_universe_refresh": ("21:30", FRI, "catch_up",
        "Guarded to the first week. Spends vendor calls, so the pass defers to "
        "api_budget's own pre-flight rather than forcing the run."),

    "run_full": ("00:30", SUN, "never_catch_up",
        "The weekly full ingest is the single largest quota consumer. "
        "Replaying it on boot during a 429 would spend budget the owner does "
        "not have, for data the vendor will not return. The miss is recorded "
        "and surfaced; the next Sunday's run is the recovery."),
    "run_results_calendar": ("22:30", FRI, "never_catch_up",
        "~500 /corporate_actions calls behind an api_budget pre-flight that "
        "self-refuses at the current 429 — a replay would either no-op or burn "
        "quota. Recorded, not replayed."),
}


def _now(now: dt.datetime | None = None) -> dt.datetime:
    return now or dt.datetime.now(dt.timezone.utc)


def _parse(ts) -> dt.datetime | None:
    try:
        d = dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def _slot_applies(cadence: str, d: dt.date) -> bool:
    wd = d.weekday()
    if cadence == DAILY:
        return True
    if cadence == WEEKDAYS:
        return wd < 5
    if cadence == SUN:
        return wd == 6
    if cadence == FRI:
        return wd == 4
    return False


def last_due_slot(name: str, now: dt.datetime | None = None) -> dt.datetime | None:
    """The most recent moment this job was supposed to have run, GRACE_MIN ago
    or older. None if it has never yet been due (a job registered today whose
    first slot is still ahead)."""
    spec = JOBS.get(name)
    if not spec:
        return None
    hh, mm = (int(x) for x in spec[0].split(":"))
    now = _now(now)
    cutoff = now - dt.timedelta(minutes=GRACE_MIN)
    d = cutoff.date()
    for _ in range(14):                      # a fortnight covers every cadence
        if _slot_applies(spec[1], d):
            slot = dt.datetime.combine(d, dt.time(hh, mm), tzinfo=dt.timezone.utc)
            if slot <= cutoff:
                return slot
        d -= dt.timedelta(days=1)
    return None


def record_run(db, name: str, now: dt.datetime | None = None) -> None:
    """Stamp a completed run. Called by the wrapper in scheduler.py for EVERY
    job, including never_catch_up ones — recording the miss is what makes it
    visible, and that is the defect being fixed."""
    try:
        row = db.query(models.KVStore).filter_by(key=KEY).first()
        state = dict(row.value or {}) if row else {}
        # When the ledger began. A slot that passed BEFORE this instant belongs
        # to a period we have no record of, and absence of a record then is
        # absence of evidence — see overdue().
        state.setdefault("_since", _now(now).isoformat(timespec="seconds"))
        entry = dict(state.get(name) or {})
        entry["last_run"] = _now(now).isoformat(timespec="seconds")
        entry.pop("claimed_at", None)        # a finished run releases its lease
        state[name] = entry
        if row:
            row.value = state                # whole-dict assign: JSON columns do
        else:                                # not track in-place mutation
            db.add(models.KVStore(key=KEY, value=state))
        db.commit()
    except Exception:
        db.rollback()                        # bookkeeping must never break a job


def _state(db) -> dict:
    try:
        row = db.query(models.KVStore).filter_by(key=KEY).first()
        return dict(row.value or {}) if row else {}
    except Exception:
        return {}


def overdue(db, now: dt.datetime | None = None) -> list[dict]:
    """Every job whose last due slot passed without a recorded run.

    A job with NO record is only overdue once a slot has genuinely gone by —
    on a fresh database nothing has run yet and nothing should alarm, the same
    unmeasured-is-not-bad discipline /api/health uses for its other signals."""
    now = _now(now)
    state = _state(db)
    out = []
    since = _parse(state.get("_since"))
    for name, (slot_s, cadence, policy, _why) in JOBS.items():
        slot = last_due_slot(name, now)
        if slot is None:
            continue
        # A ledger that has never been written knows nothing, and a slot older
        # than the ledger itself was never observed — on a freshly provisioned
        # box that is EVERY job, and reporting 15 misses on first boot is how an
        # alert teaches its reader to ignore it. Jobs become eligible as their
        # first post-install slot goes by.
        if since is None or slot < since:
            continue
        last = _parse((state.get(name) or {}).get("last_run"))
        if last is not None and last >= slot:
            continue
        out.append({"job": name, "policy": policy, "due_at": slot.isoformat(timespec="seconds"),
                    "last_run": last.isoformat(timespec="seconds") if last else None,
                    "missed_min": int((now - slot).total_seconds() // 60)})
    out.sort(key=lambda r: -r["missed_min"])
    return out


def is_catchable(rec: dict, now: dt.datetime | None = None) -> bool:
    """Whether an overdue job should actually be REPLAYED.

    Policy first, then the one job-shaped exception: run_encrypted_backup writes
    under backups/YYYY-MM-DD/, so replaying it during its own UTC day overwrites
    that prefix (idempotent, recovers the day), while replaying it tomorrow just
    writes tomorrow's backup and cannot reconstruct what was missed. Catching it
    up outside its day would be motion without recovery."""
    if rec["policy"] != "catch_up":
        return False
    if rec["job"] == "run_encrypted_backup":
        slot = _parse(rec["due_at"])
        return bool(slot and slot.date() == _now(now).date())
    return True


def claim(db, name: str, now: dt.datetime | None = None) -> bool:
    """Exclusive right to catch `name` up, or False. SELECT … FOR UPDATE
    serialises the read-decide-write across containers (SQLite ignores it and is
    single-writer anyway); the lease covers an instance killed mid-run. Same
    shape as eod_coverage.claim, deliberately — one lease pattern in the tree."""
    now = _now(now)
    try:
        row = db.query(models.KVStore).filter_by(key=KEY).with_for_update().first()
        if row is None:
            db.add(models.KVStore(key=KEY, value={}))
            db.commit()
            row = db.query(models.KVStore).filter_by(key=KEY).with_for_update().first()
            if row is None:
                return False
        state = dict(row.value or {})
        entry = dict(state.get(name) or {})
        held = _parse(entry.get("claimed_at"))
        if held and (now - held) < dt.timedelta(minutes=LEASE_MIN):
            db.rollback()
            return False
        entry["claimed_at"] = now.isoformat(timespec="seconds")
        state[name] = entry
        row.value = state
        db.commit()
        return True
    except Exception:
        db.rollback()
        return False
