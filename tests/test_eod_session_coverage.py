"""The 14 Aug 2026 EOD session that never landed — and stayed invisible.

The Dhan top-up wrote NOTHING for that session. scheduler.py drives `schedule`
with `while True: run_pending()` and no persistence, so every job's next_run is
recomputed from PROCESS START: a container recreated after the 10:15 UTC slot —
every deploy after 15:45 IST — silently drops that day's run, nothing retries
it, and nothing records that it did not happen.

What turned one missed run into three invisible days was the SHAPE of what was
there: 21 brand-new listings (AASTHA, TURTLEMINT, XTRANET…, ~24 rows each) had
been seeded under 14-Aug by the daily history self-heal, against ~1013 names on
every prior session. max(date) — the only price-freshness signal /api/health
had — therefore read 0 days old while 992 names had no candle for the session
at all. The repair was a single backfill_prices() call: the job was healthy, it
simply had not run.

`test_partial_session_is_invisible_to_max_date` is the one that fails without
the fix: it builds exactly the 21-vs-1013 shape and shows the old signal
reporting a current feed over it.

Every threshold here is asserted RELATIVE. The universe drifts (1009 → 1013 in
the last few weeks alone), so an absolute floor is either wrong today or wrong
in a month; the pair of counts is the signal and the ratio is the rule.
"""
import datetime as dt
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import eod_coverage as E
from app import models
from app.database import Base

UTC = dt.timezone.utc


def _at(day: str, hour: int, minute: int = 0) -> dt.datetime:
    """A fixed UTC instant. Every timing rule below is asserted against a stated
    weekday and minute rather than against whenever the suite happens to run."""
    y, m, d = (int(x) for x in day.split("-"))
    return dt.datetime(y, m, d, hour, minute, tzinfo=UTC)


@pytest.fixture()
def db(tmp_path):
    """A PRIVATE, fully-migrated sqlite file. ~20 test files call
    Base.metadata.drop_all() on the process-wide engine and this file's whole
    subject is counts over historical_prices — the hermeticity lesson in
    tests/test_health_surfaces_vendor_failure.py's docstring, applied up front
    rather than after a mystery failure under the full suite."""
    eng = create_engine(f"sqlite:///{tmp_path / 'eod.db'}",
                        connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    try:
        yield s
    finally:
        s.close()
        eng.dispose()


def _session(db, date: str, n_names: int) -> None:
    """Give the first `n_names` companies a close for `date`. Company rows are
    created once and reused across dates, so the only thing varying between
    sessions is how many of them got a candle — the incident's shape exactly.
    Re-callable for the same date (the repair step) without tripping the
    (company_id, date) unique key."""
    have = {cid for (cid,) in db.query(models.Company.id).all()}
    for i in range(1, n_names + 1):
        if i not in have:
            db.add(models.Company(id=i, ticker=f"TST{i:05d}", name=f"Test {i}",
                                  type="nonfinancial", sector="IT",
                                  shares_outstanding=1.0))
    db.commit()
    dated = {cid for (cid,) in db.query(models.HistoricalPrice.company_id)
                                 .filter(models.HistoricalPrice.date == date).all()}
    db.bulk_save_objects([models.HistoricalPrice(company_id=i, date=date, close=100.0 + i)
                          for i in range(1, n_names + 1) if i not in dated])
    db.commit()


# ── The coverage signal: what max(date) cannot see ───────────────────────────

def test_partial_session_is_invisible_to_max_date(db):
    """THE regression test. 1013 names on 13-Aug, 21 on 14-Aug: the newest date
    IS 14-Aug, so price_age_days' only input says the feed is current — and 992
    names have no candle for that session. Only the name count sees it."""
    _session(db, "2026-08-13", 1013)
    _session(db, "2026-08-14", 21)
    assert E.latest_stored_session(db) == "2026-08-14"      # price_age_days == 0

    cov = E.session_coverage(db, _at("2026-08-17", 9))      # the Monday it was found
    assert cov == {"date": "2026-08-14", "names": 21,
                   "prior_date": "2026-08-13", "prior_names": 1013}
    # The exact arithmetic uptime.yml runs on the two published fields.
    assert cov["names"] * 100 < cov["prior_names"] * E.MIN_COVERAGE_PCT


@pytest.mark.parametrize("prior_names,names", [(1009, 1013), (1013, 1009)])
def test_universe_drift_is_not_a_partial_session(db, prior_names, names):
    """Why the rule is a ratio and not a floor: the universe moved 1009 → 1013
    over recent weeks (index churn, IPO onboarding, a tranche onboarded), and it
    moves back. Any absolute number low enough to pass 1009 is also low enough
    to pass a session that lost two thirds of the universe."""
    _session(db, "2026-08-13", prior_names)
    _session(db, "2026-08-14", names)
    cov = E.session_coverage(db, _at("2026-08-17", 9))
    assert cov["names"] == names and cov["prior_names"] == prior_names
    assert cov["names"] * 100 >= cov["prior_names"] * E.MIN_COVERAGE_PCT


def test_empty_and_single_session_databases_measure_none(db):
    """A freshly provisioned database must not alarm — the same None-vs-exception
    discipline /api/health already applies to every unmeasured signal. No prices
    at all is all-None; one session on record has no denominator to divide by,
    which is unmeasured, not a finding. Zero would be the dangerous answer: to
    the ratio gate it reads as a total EOD failure."""
    assert E.session_coverage(db, _at("2026-08-17", 12)) == {
        "date": None, "names": None, "prior_date": None, "prior_names": None}
    _session(db, "2026-08-17", 1013)
    cov = E.session_coverage(db, _at("2026-08-17", 12))
    assert cov["date"] == "2026-08-17" and cov["names"] == 1013
    assert cov["prior_date"] is None and cov["prior_names"] is None


def test_todays_session_is_not_graded_until_its_write_window_closes(db):
    """The top-up walks ~1,000 names one self-rate-limited REST call at a time,
    so a 30-minute probe landing mid-run would page about a partial write that
    is about to complete. Until slot + SETTLE_GRACE_MIN we grade the PREVIOUS
    session, whose count can no longer change; after it, today's."""
    _session(db, "2026-08-12", 1013)
    _session(db, "2026-08-13", 1013)
    _session(db, "2026-08-14", 300)             # in flight, 16:00 IST
    mid = E.session_coverage(db, _at("2026-08-14", 10, 30))
    assert mid["date"] == "2026-08-13" and mid["names"] == 1013
    settled = E.session_coverage(db, _at("2026-08-14", 11, 30))
    assert settled["date"] == "2026-08-14" and settled["names"] == 300


# ── "Most recent completed session", without a market calendar ───────────────

@pytest.mark.parametrize("day,hour,minute,expected", [
    ("2026-08-17", 9, 0, "2026-08-14"),    # Mon 14:30 IST — Monday's close has not happened
    ("2026-08-17", 11, 0, "2026-08-17"),   # Mon 16:30 IST — it has
    ("2026-08-15", 12, 0, "2026-08-14"),   # Sat → Friday
    ("2026-08-16", 23, 0, "2026-08-14"),   # Sun → Friday
    ("2026-08-14", 10, 59, "2026-08-13"),  # one minute short of slot + grace
    ("2026-08-14", 11, 0, "2026-08-14"),   # …and one minute into it
    ("2026-08-14", 2, 0, "2026-08-13"),    # 07:30 IST, hours before the open
])
def test_last_completed_session_needs_no_market_calendar(day, hour, minute, expected):
    """Weekends are arithmetic, and today counts only once its close plus the
    grace has passed. Holidays are deliberately NOT excluded — a holiday list is
    a thing to maintain and a stale one is worse than none — so this can name a
    date the exchange never opened; MAX_ATTEMPTS is what stops that looping."""
    assert E.last_completed_session(_at(day, hour, minute)).isoformat() == expected


# ── The catch-up decision ────────────────────────────────────────────────────

def test_pending_session_is_the_missed_slot_and_nothing_else(db):
    _session(db, "2026-08-13", 1013)
    # Friday, market still open: today's close has not happened, nothing is owed.
    assert E.pending_session(db, _at("2026-08-14", 9)) is None
    # Friday after slot + grace with no 14-Aug candle: exactly the miss.
    assert E.pending_session(db, _at("2026-08-14", 12)) == "2026-08-14"
    # The weekend does not invent two more missed sessions.
    assert E.pending_session(db, _at("2026-08-16", 12)) == "2026-08-14"
    _session(db, "2026-08-14", 1013)
    assert E.pending_session(db, _at("2026-08-16", 12)) is None


def test_pending_session_fires_on_a_PARTIAL_session_too(db):
    """The incident's own shape, and why this cannot key on max(date) alone:
    14-Aug was PRESENT (21 seeded listings), so "is the newest date the due
    session" answers yes and the catch-up would never have run for the very case
    it exists for. It fires on coverage, at the same threshold the alert uses —
    and holds off while the session could still be mid-write."""
    _session(db, "2026-08-13", 1013)
    _session(db, "2026-08-14", 21)
    assert E.latest_stored_session(db) == "2026-08-14"          # date says "current"
    assert E.pending_session(db, _at("2026-08-14", 11, 29)) is None   # still writing
    assert E.pending_session(db, _at("2026-08-14", 11, 30)) == "2026-08-14"
    assert E.pending_session(db, _at("2026-08-17", 9)) == "2026-08-14"
    _session(db, "2026-08-14", 1013)                            # the repair
    assert E.pending_session(db, _at("2026-08-17", 9)) is None


def test_an_empty_price_table_owes_nothing(db):
    """A database that has never been seeded is not a missed session — seeding is
    run_missing_history_backfill's job (5y per name), not a 30-day top-up's. A
    fresh deploy must not "self-heal" a universe it has no history for."""
    assert E.pending_session(db, _at("2026-08-17", 12)) is None


# ── The claim: one runner, and a brake for closed exchanges ──────────────────

def test_claim_is_exclusive_within_its_lease(db):
    """Every deploy boots containers together and a cutover can briefly run two
    schedulers. One catch-up is ~1,000 REST calls; N at once is a self-inflicted
    rate-limit that would fail all of them."""
    t0 = _at("2026-08-17", 11)
    assert E.claim(db, "2026-08-14", t0) is True
    assert E.claim(db, "2026-08-14", t0) is False
    assert E.claim(db, "2026-08-14",
                   t0 + dt.timedelta(minutes=E.LEASE_MIN - 1)) is False
    assert E.claim(db, "2026-08-14",
                   t0 + dt.timedelta(minutes=E.LEASE_MIN + 1)) is True


def test_claim_gives_up_on_a_date_the_exchange_never_held(db):
    """The holiday brake. On a shut day there is no candle to fetch, the top-up
    adds 0 rows, and pending_session keeps naming the same date — so without a
    cap the interval job retries for ever and logs a failure for ever. Giving up
    costs nothing even in a real vendor outage: the next daily
    run_dhan_topup() asks for a 30-DAY window and refills the gap."""
    t0 = _at("2026-08-17", 11)
    taken = [E.claim(db, "2026-08-14", t0 + dt.timedelta(minutes=(E.LEASE_MIN + 1) * k))
             for k in range(E.MAX_ATTEMPTS + 2)]
    assert taken == [True] * E.MAX_ATTEMPTS + [False, False]
    # …and a NEW target starts on a full budget — the brake is per date.
    assert E.claim(db, "2026-08-17",
                   t0 + dt.timedelta(minutes=(E.LEASE_MIN + 1) * 6)) is True


def test_record_result_leaves_the_attempt_budget_alone(db):
    """It is a record, not a control. Resetting `attempts` on rows_added > 0
    would reopen the loop this brake closes: a top-up can add candles for OTHER
    dates inside its 30-day window while the target session stays absent, and
    the counter would then never reach the cap."""
    t0 = _at("2026-08-17", 11)
    assert E.claim(db, "2026-08-14", t0) is True
    E.record_result(db, "2026-08-14", 992, t0 + dt.timedelta(minutes=8))
    state = db.query(models.KVStore).filter_by(key=E.KEY).first().value
    assert state["attempts"] == 1 and state["last_rows_added"] == 992
    assert state["last_run_at"].startswith("2026-08-17T11:08")


# ── The published surface, end to end ────────────────────────────────────────

@pytest.fixture()
def hermetic_app(tmp_path):
    """The real app with get_db bound to a private, migrated sqlite file — same
    fixture and same reason as tests/test_health_surfaces_vendor_failure.py."""
    from sqlalchemy.orm import sessionmaker as _sm
    from app.database import get_db
    from app.main import app
    eng = create_engine(f"sqlite:///{tmp_path / 'health.db'}",
                        connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    SL = _sm(bind=eng)

    def _override():
        s = SL()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override
    try:
        yield app, SL
    finally:
        app.dependency_overrides.pop(get_db, None)
        eng.dispose()


def test_health_publishes_the_stamped_counts(hermetic_app):
    """/api/health reads the pair off the heartbeat row the scheduler stamps —
    O(1), the SCALE-03 contract price_age_days already lives under — and reports
    a partial session as 21-vs-1013 while price_age_days reads 0."""
    from fastapi.testclient import TestClient
    app, SL = hermetic_app
    s = SL()
    s.add(models.KVStore(key="scheduler_heartbeat", value={
        "ts": dt.datetime.now(UTC).isoformat(timespec="seconds"),
        "latest_eod_date": dt.date.today().isoformat(),
        "eod_names": 21, "eod_names_prior": 1013,
        "eod_names_date": dt.date.today().isoformat()}))
    s.commit()
    s.close()
    body = TestClient(app).get("/api/health").json()
    assert body["price_age_days"] == 0          # the signal that was blind
    assert body["eod_names"] == 21 and body["eod_names_prior"] == 1013
    # A signal, not a degrade — the threshold lives in uptime.yml, the same
    # split price_age_days and integrity already use.
    assert body["status"] == "ok"


def test_health_reports_the_pair_as_none_before_the_scheduler_stamps_it(hermetic_app):
    """A heartbeat written by a scheduler that predates this change carries no
    counts, and uptime.yml takes effect on MERGE while the backend deploy is
    MANUAL. Absent must read as unmeasured (null), never as zero — zero is a
    total EOD failure to the ratio gate."""
    from fastapi.testclient import TestClient
    app, SL = hermetic_app
    s = SL()
    s.add(models.KVStore(key="scheduler_heartbeat", value={
        "ts": dt.datetime.now(UTC).isoformat(timespec="seconds")}))
    s.commit()
    s.close()
    body = TestClient(app).get("/api/health").json()
    assert body["eod_names"] is None and body["eod_names_prior"] is None
    assert body["status"] == "ok"


def test_the_alert_and_the_repair_share_one_threshold():
    """uptime.yml must gate the fields health publishes, at the SAME percentage
    pending_session repairs at. If the two drift apart, the alert fires on
    sessions the self-heal considers fine — or, worse, stays silent on ones it
    is quietly retrying three times a day."""
    wf = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      ".github", "workflows", "uptime.yml")
    body = open(wf).read()
    assert ".eod_names" in body and ".eod_names_prior" in body
    assert f"* {E.MIN_COVERAGE_PCT})" in body
