"""
app/eod_coverage.py — did the EOD session land, and did it land for the WHOLE
universe?

17 Aug 2026 incident. The Dhan EOD top-up wrote NOTHING for the 14 Aug session.
Root cause: scheduler.py drives the `schedule` library with
`while True: run_pending()` and no persistence, so every job's next_run is
recomputed from PROCESS START — a container recreated after the 10:15 UTC slot
(i.e. every deploy after 15:45 IST) silently drops that day's run. Nothing
retried it and nothing recorded that it had not happened.

It stayed invisible for three days because the table's newest DATE still read
current: the daily history self-heal seeded 21 brand-new listings (AASTHA,
TURTLEMINT, XTRANET…, ~24 rows each) under 14-Aug, so max(date) — the only
price-freshness signal /api/health had — reported 0 days old while 992 of ~1013
names had no candle for the session at all. The repair was one
backfill_prices() call: the job was healthy, it simply had not run.

Two answers to that, kept OUT of scheduler.py because importing that module
runs its whole boot sequence:

  * `session_coverage()` — the name COUNT behind the newest settled session and
    the one before it. max(date) cannot see a partial session; a count against
    the previous session's can. Returned as a PAIR and never as a verdict: the
    universe drifts (1009 → 1013 over recent weeks), so the caller divides, and
    any absolute floor would be wrong today or wrong in a month.
  * `pending_session()` + `claim()`/`record_result()` — the catch-up. It asks
    only whether the most recent completed session is in the table, which is
    true after a missed slot however the slot came to be missed. No
    restart-timing arithmetic to get wrong, and idempotent, so the same call is
    safe on boot AND on an interval.

NO MARKET CALENDAR. A holiday list is a thing to maintain and a stale one is
worse than none, so weekends are arithmetic and holidays are treated the way
the rest of the codebase already treats them — as "the vendor has no candle for
that date", which is an ANSWER (bounded by MAX_ATTEMPTS below), not a failure
to retry. Same tolerance /api/health encodes by calling a price_age_days of 1-3
normal.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import distinct, func
from sqlalchemy.orm.attributes import flag_modified

from . import models

# The daily EOD slot scheduler.py registers run_prices at: 10:15 UTC = 15:45
# IST, ~15 min after the NSE close. Duplicated rather than imported, for the
# reason in the module docstring.
SLOT_UTC_MIN = 10 * 60 + 15
# …before today counts as a completed session that is DUE. Long enough for the
# scheduled run and its ~1,000 self-rate-limited REST calls to have finished, so
# the catch-up never races the job it stands in for and never fires while the
# market is still open.
DUE_GRACE_MIN = 45
# …before we GRADE today's coverage. Longer still: a top-up in flight has
# written some names and not others, and a 30-minute alerting probe landing in
# that window would page about a partial write that is about to complete. Until
# it closes we grade the PREVIOUS session, whose count can no longer change.
SETTLE_GRACE_MIN = 75
# What "partial" means, as a percentage of the previous session's name count.
# Shared by BOTH halves of the fix — .github/workflows/uptime.yml gates on this
# number and pending_session() repairs on it — so the alert can never fire on a
# session the catch-up considers fine, or stay silent on one it is quietly
# retrying. 80% is far below any honest universe drift and far above the 2%
# (21/1013) this exists to catch.
MIN_COVERAGE_PCT = 80

KEY = "eod_selfheal_v1"
# How long another instance's claim is respected. Every deploy boots containers
# together and a cutover can briefly run two schedulers; one catch-up is ~1,000
# REST calls, and N of them at once is a self-inflicted rate-limit that would
# fail all of them.
LEASE_MIN = 30
# Attempts per target date before we stop. This is the holiday brake: on a day
# the exchange was shut there is no candle to fetch, the top-up adds 0 rows,
# pending_session keeps naming the same date, and without a cap the interval job
# would retry for ever and log a failure for ever. Giving up costs nothing even
# in a genuine vendor outage — the next daily run_dhan_topup() asks for a
# 30-DAY window and refills the gap.
MAX_ATTEMPTS = 3


def _now(now: dt.datetime | None = None) -> dt.datetime:
    """UTC now, with a seam so tests can stand at a chosen minute of a chosen
    weekday instead of waiting for one."""
    return now or dt.datetime.now(dt.timezone.utc)


def _parse(ts) -> dt.datetime | None:
    try:
        d = dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None                      # unparseable stamp → treated as no claim
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def last_completed_session(now: dt.datetime | None = None) -> dt.date:
    """The most recent date NSE could have closed a session on: the newest
    weekday at or before today, counting TODAY only once the EOD slot plus
    DUE_GRACE_MIN has passed. Holidays are NOT excluded — nothing here knows
    them — so this can name a date the exchange never opened; MAX_ATTEMPTS is
    what keeps that from becoming a loop."""
    now = _now(now)
    d = now.date()
    if now.hour * 60 + now.minute < SLOT_UTC_MIN + DUE_GRACE_MIN:
        d -= dt.timedelta(days=1)        # today's candle is not final yet
    while d.weekday() >= 5:              # Sat/Sun are never sessions
        d -= dt.timedelta(days=1)
    return d


def latest_stored_session(db) -> str | None:
    """max(date) over the price table — the number /api/health reports the age
    of, and the one the incident proved insufficient on its own."""
    latest = db.query(func.max(models.HistoricalPrice.date)).scalar()
    return str(latest)[:10] if latest else None


def session_coverage(db, now: dt.datetime | None = None) -> dict:
    """{'date','names','prior_date','prior_names'} for the newest SETTLED
    session. Counts NAMES (distinct company_id) — the (company_id, date) unique
    key makes that equal to a row count today, and a name count is what the
    ratio means if it ever stops being.

    Every field is None on an empty table, and prior_* is None when only one
    session is on record: a ratio with no denominator is unmeasured, not a
    finding, and a freshly provisioned database must not page anyone. Zero would
    be the dangerous answer — to the ratio gate it reads as a total EOD failure.

    One grouped pass over the tail of historical_prices. The caller (the
    scheduler heartbeat) throttles it and stamps the result, because /api/health
    must stay O(1) — SCALE-03's lesson."""
    now = _now(now)
    empty = {"date": None, "names": None, "prior_date": None, "prior_names": None}
    q = db.query(models.HistoricalPrice.date,
                 func.count(distinct(models.HistoricalPrice.company_id)))
    if now.hour * 60 + now.minute < SLOT_UTC_MIN + SETTLE_GRACE_MIN:
        # Today's session may still be half-written — grade the last one that
        # can no longer change. Dates are ISO strings, so `<` is chronological.
        q = q.filter(models.HistoricalPrice.date < now.date().isoformat())
    rows = (q.group_by(models.HistoricalPrice.date)
             .order_by(models.HistoricalPrice.date.desc()).limit(2).all())
    if not rows:
        return empty
    out = dict(empty)
    out["date"], out["names"] = str(rows[0][0])[:10], int(rows[0][1])
    if len(rows) > 1:
        out["prior_date"], out["prior_names"] = str(rows[1][0])[:10], int(rows[1][1])
    return out


def pending_session(db, now: dt.datetime | None = None) -> str | None:
    """The session date the EOD top-up owes us, or None when nothing is due."""
    latest = latest_stored_session(db)
    if latest is None:
        # An EMPTY price table is not a missed session, it is a database that
        # has never been seeded — and seeding is run_missing_history_backfill's
        # job (5y per name), not a 30-day top-up's. A target here would have
        # every fresh deploy "self-heal" a universe it has no history for.
        return None
    due = last_completed_session(now).isoformat()
    if latest < due:
        return due
    # …or the due session is PRESENT but PARTIAL. This branch is the incident
    # itself: 14-Aug WAS the newest date, because the history self-heal had
    # seeded 21 new listings under it, so a max(date) test answers "current" and
    # the catch-up would never run for the one case it exists for. Graded on the
    # same threshold the alert uses, and only once session_coverage says the
    # session has settled — a top-up in flight is not a top-up that failed.
    cov = session_coverage(db, now)
    if (cov["date"] == due and cov["prior_names"]
            and cov["names"] * 100 < cov["prior_names"] * MIN_COVERAGE_PCT):
        return due
    return None


def claim(db, target: str, now: dt.datetime | None = None) -> bool:
    """Take the exclusive right to run the catch-up for `target`, or return
    False. Two reasons to refuse: another instance holds a claim younger than
    LEASE_MIN (the deploy stampede), or `target` has already been tried
    MAX_ATTEMPTS times (the holiday brake) — both above.

    SELECT … FOR UPDATE is the mutex, so the read-decide-write is serialised
    across containers on Postgres; SQLite (tests, local dev) ignores the clause
    and is single-writer anyway. The claim COMMITS before the caller starts the
    top-up: the lock guards the DECISION, not the ten minutes of REST calls
    after it, and a container killed mid-run must not leave a row locked — the
    lease is what covers that."""
    now = _now(now)
    row = db.query(models.KVStore).filter_by(key=KEY).with_for_update().first()
    if row is None:
        try:
            db.add(models.KVStore(key=KEY, value={}))
            db.commit()
        except Exception:
            db.rollback()                # a racing instance inserted it first
        row = db.query(models.KVStore).filter_by(key=KEY).with_for_update().first()
        if row is None:
            return False
    state = dict(row.value or {})
    attempts = 0
    if state.get("target") == target:
        attempts = int(state.get("attempts") or 0)
        if attempts >= MAX_ATTEMPTS:
            db.rollback()                # release the lock; nothing to write
            return False
        held = _parse(state.get("claimed_at"))
        if held and (now - held).total_seconds() < LEASE_MIN * 60:
            db.rollback()
            return False
    state.update(target=target, attempts=attempts + 1,
                 claimed_at=now.isoformat(timespec="seconds"))
    row.value = state
    flag_modified(row, "value")          # DATA-11: JSON columns need it explicitly
    db.commit()
    return True


def record_result(db, target: str, rows_added: int,
                  now: dt.datetime | None = None) -> None:
    """Stamp what the catch-up actually produced, so the next investigation can
    tell "the exchange was shut" from "nobody has tried yet".

    A RECORD, not a control: it deliberately leaves `attempts` alone. Resetting
    it on rows_added > 0 would reopen the loop MAX_ATTEMPTS closes — a top-up
    can add candles for OTHER dates inside its 30-day window while the target
    session stays absent, and the counter would then never reach the cap. A
    target that did land needs no counter: pending_session stops naming it."""
    now = _now(now)
    row = db.query(models.KVStore).filter_by(key=KEY).with_for_update().first()
    if row is None:
        return
    state = dict(row.value or {})
    state.update(target=target,
                 last_run_at=now.isoformat(timespec="seconds"),
                 last_rows_added=int(rows_added))
    row.value = state
    flag_modified(row, "value")
    db.commit()
