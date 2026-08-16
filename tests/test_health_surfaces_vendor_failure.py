"""A total vendor outage must not report as healthy — and neither must a DB one.

14 Aug 2026: every IndianAPI call failed for 5+ hours while /api/health returned
{"status":"ok","errors_1h":0}. _get() serves its last good payload on upstream
failure, so no exception ever reached the error log — the reporting was honest
about errors and silent about reachability. These tests pin the distinction.

The converse, and why the unreachable rule moved off the since-boot counters:
it shipped as `fail > 0` (cumulative, never decays) AND a 30-minute-old last
SUCCESS, so after any one transient failure it meant nothing but "nobody has
called the vendor successfully in half an hour" — true of every quiet night,
since no keepalive touches the vendor and uptime.yml only probes /api/health.
It emailed the owner about silence. The rule now keys on the last FAILURE's
recency too (tests below).

Second gap, same shape: get_db() connects lazily, so with the DATABASE down every
health signal failed inside its own `except Exception: pass`, went out as null,
and uptime.yml's `// 0` read null as the passing value — 200/"ok" through a
total DB outage. Health now reports a raising signal as "unmeasured" and the
status as "degraded" (tests below). Keyed on the EXCEPTION, not on None: a fresh
DB with no heartbeat row measures None honestly and stays "ok".

TEST ISOLATION (why the `hermetic_app` fixture exists): the first version of the
degrade-on-exception change was reverted because this file's route test failed
ONLY under the full suite. Root cause: ~20 test files call
`Base.metadata.drop_all(engine)` on the process-wide engine (conftest points
every module at ONE per-run sqlite file; their own `os.environ["DATABASE_URL"]`
lines are no-ops once app.database is imported). The last of them before this
file in collection order (test_fix08_lender_and_identity.py's `db` fixture)
leaves the database with only `alembic_version`, so `TestClient(app)` served
health from a DB with no kv_store / historical_prices, the queries raised
"no such table", and the "clean container must be ok" assertion got "degraded" —
which is the CORRECT answer for a DB with its tables gone. The old code hid that
state as ok; the fix exposes it; the test must therefore bring its own tables.
"""
import importlib

import pytest


def _fresh():
    from app import vendor_meter as vm
    importlib.reload(vm)
    return vm


@pytest.fixture()
def hermetic_app(tmp_path):
    """The real FastAPI app with get_db bound to a PRIVATE, fully-migrated
    sqlite file — immune to whichever earlier test last dropped the shared
    engine's tables (see module docstring). Yields (app, sessionmaker)."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.database import Base, get_db
    from app.main import app

    eng = create_engine(f"sqlite:///{tmp_path / 'health.db'}",
                        connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    SL = sessionmaker(autocommit=False, autoflush=False, bind=eng)

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


def test_no_calls_is_not_degraded():
    """A container that made no vendor calls stays ok — no evidence is not bad
    evidence, and this is the normal state for a quiet scheduler."""
    vm = _fresh()
    o = vm.outcomes()
    assert o == {"ok": 0, "fail": 0, "last_ok_min": None, "last_fail_min": None}
    assert not vm.unreachable()
    assert vm.recent() == {"n": 0, "fail": 0, "fail_pct": None}
    assert not vm.failing()


def test_failures_with_no_success_degrade():
    """The exact 14 Aug shape: calls happening, all failing, none succeeding."""
    vm = _fresh()
    for _ in range(5):
        vm.record(False)
    o = vm.outcomes()
    assert o["fail"] == 5 and o["ok"] == 0 and o["last_ok_min"] is None
    assert vm.unreachable()


def test_recent_success_is_not_degraded():
    """Flaky calls alongside a recent success must NOT page anyone."""
    vm = _fresh()
    vm.record(False)
    vm.record(True)
    o = vm.outcomes()
    assert o["last_ok_min"] == 0
    assert not vm.unreachable()


def test_health_endpoint_reports_degraded(hermetic_app):
    """End-to-end through the real route, not just the helper — the earlier
    watchlist and backtest lessons: a helper-only test passes with the call site
    deleted."""
    from fastapi.testclient import TestClient
    app, _ = hermetic_app
    vm = _fresh()
    c = TestClient(app)

    body = c.get("/api/health").json()
    assert body["status"] == "ok", "clean container must be ok"
    assert body["vendor_fail"] == 0

    for _ in range(3):
        vm.record(False)
    body = c.get("/api/health").json()
    assert body["status"] == "degraded", "total vendor outage must not read as ok"
    assert body["degraded_reason"] == "vendor_unreachable"
    assert body["vendor_fail"] == 3 and body["vendor_last_ok_min"] is None

    vm.record(True)
    body = c.get("/api/health").json()
    assert body["status"] == "ok", "a fresh success must clear the degrade"
    assert body["vendor_last_ok_min"] == 0


def test_get_actually_records_outcomes(monkeypatch):
    """The call site, not just the helper.

    Every test above drives vendor_meter directly, so all four would still pass
    if market_routes._get never called record() — and then health would never
    degrade in production, which is the entire point. This drives a real _get.
    """
    import importlib
    from app import vendor_meter as vm
    from app import market_routes as M
    importlib.reload(vm)

    class _Resp:
        status_code = 500
        def json(self): return {}

    monkeypatch.setattr(M.requests, "get", lambda *a, **k: _Resp())
    M._cache.clear()
    M._get("/__probe_fail__")
    assert vm.outcomes()["fail"] >= 1, "_get must record a failed call"

    class _OK:
        status_code = 200
        def json(self): return {"x": 1}

    monkeypatch.setattr(M.requests, "get", lambda *a, **k: _OK())
    M._cache.clear()
    M._get("/__probe_ok__")
    o = vm.outcomes()
    assert o["ok"] >= 1 and o["last_ok_min"] == 0, "_get must record a successful call"


# ── Quiet night vs live outage: the rule keys on the last FAILURE ────────────
#
# `fail > 0 AND last_ok_min >= 30` was armed for the life of the process by the
# container's first transient failure, after which it only asked whether any
# vendor call had SUCCEEDED in the last half hour. The web process calls the
# vendor only when a user hits a market route and nothing keeps it warm, so an
# empty Indian night matched it and emailed the owner about a healthy system.
# These tests pin both directions: a live outage alerts, silence does not.

def _age(vm, *, fail_min=None, ok_min=None):
    """Backdate the meter's last-failure / last-success stamps by N minutes —
    the alternative is sleeping for half an hour in CI."""
    import time as _t
    now = _t.time()
    if fail_min is not None:
        vm._last_fail = now - fail_min * 60
    if ok_min is not None:
        vm._last_ok = now - ok_min * 60


def test_quiet_night_after_an_old_failure_is_not_unreachable():
    """The defect itself: one transient failure, no success ever, then hours of
    no traffic. The OLD expression is asserted here too — it still fires on this
    state, which is exactly why an idle container paged the owner."""
    vm = _fresh()
    vm.record(False)
    _age(vm, fail_min=90)
    o = vm.outcomes()
    assert o["fail"] == 1 and o["last_ok_min"] is None and o["last_fail_min"] == 90
    assert o["fail"] > 0 and (o["last_ok_min"] is None or o["last_ok_min"] >= 30), \
        "the rule this replaces fires on an idle container — the bug"
    assert not vm.unreachable(), "90 min with no calls is silence, not an outage"
    assert not vm.failing(), "one outcome is far below the ring's floor"


def test_fresh_failures_with_no_success_are_unreachable():
    """The 14 Aug shape, unchanged: calls are happening and all of them fail."""
    vm = _fresh()
    for _ in range(3):
        vm.record(False)
    assert vm.unreachable()


def test_a_success_after_the_last_failure_clears_it():
    vm = _fresh()
    vm.record(False)
    assert vm.unreachable()
    vm.record(True)
    assert not vm.unreachable(), "upstream answered after the failure — it is reachable"


def test_failures_after_a_recent_success_are_unreachable():
    """The old rule's other half was a false NEGATIVE: it only asked how old the
    last SUCCESS was, so a success 5 minutes ago let any number of failures
    since read "ok" until the half hour elapsed. What matters is that nothing
    has succeeded SINCE the failure."""
    vm = _fresh()
    vm.record(True)
    _age(vm, ok_min=5)
    for _ in range(3):
        vm.record(False)
    o = vm.outcomes()
    assert o["last_ok_min"] == 5
    assert not (o["fail"] > 0 and o["last_ok_min"] >= 30), "the old rule stayed quiet here"
    assert vm.unreachable()


def test_ordering_uses_raw_stamps_not_rounded_minutes():
    """A success and a failure inside the same wall-clock minute both report an
    age of 0, so a rule comparing outcomes()' minute ages could not tell which
    came last. unreachable() compares the stamps, where the order lives."""
    vm = _fresh()
    vm.record(True)
    vm.record(False)
    o = vm.outcomes()
    assert o["last_ok_min"] == 0 and o["last_fail_min"] == 0
    assert vm.unreachable()


def test_the_window_closes_at_thirty_minutes():
    """29 minutes is a live outage; 30 is history. The boundary is the FAILURE's
    age — the success's age no longer decides anything on its own."""
    vm = _fresh()
    vm.record(False)
    _age(vm, fail_min=29)
    assert vm.unreachable()
    _age(vm, fail_min=30)
    assert not vm.unreachable()


def test_health_endpoint_is_ok_on_a_quiet_night(hermetic_app):
    """End-to-end through the real route, because this file's standing lesson is
    that a helper-only test passes with the call site untouched: /api/health
    itself must stop reporting an idle container as an outage, while still
    reporting its failure count honestly."""
    from fastapi.testclient import TestClient
    app, _ = hermetic_app
    vm = _fresh()
    c = TestClient(app)

    vm.record(False)
    body = c.get("/api/health").json()
    assert body["status"] == "degraded", "a failure with nothing after it is the live case"
    assert body["degraded_reason"] == "vendor_unreachable"

    _age(vm, fail_min=90)
    body = c.get("/api/health").json()
    assert body["status"] == "ok" and "degraded_reason" not in body, \
        "no traffic for 90 min must not email the owner"
    assert body["vendor_fail"] == 1, "the since-boot counter still tells the truth"
    assert body["vendor_last_ok_min"] is None


def test_health_endpoint_degrades_on_failures_after_a_recent_success(hermetic_app):
    """Route-level for the false negative: vendor_last_ok_min is 5, well inside
    the old 30-minute grace, and everything since has failed."""
    from fastapi.testclient import TestClient
    app, _ = hermetic_app
    vm = _fresh()
    c = TestClient(app)
    vm.record(True)
    _age(vm, ok_min=5)
    for _ in range(3):
        vm.record(False)
    body = c.get("/api/health").json()
    assert body["vendor_last_ok_min"] == 5
    assert body["status"] == "degraded"
    assert body["degraded_reason"] == "vendor_unreachable"


# ── Failure-rate floor (the ring) ────────────────────────────────────────────

def test_ring_floor_trips_on_high_recent_failure_despite_a_success():
    """The hole the floor closes: 19 failures then ONE success. The
    unreachable rule clears (last_ok_min == 0); the ring must not."""
    vm = _fresh()
    for _ in range(19):
        vm.record(False)
    vm.record(True)
    o = vm.outcomes()
    assert o["last_ok_min"] == 0
    assert not vm.unreachable()
    r = vm.recent()
    assert r == {"n": 20, "fail": 19, "fail_pct": 0.95}
    assert vm.failing()


def test_ring_floor_needs_enough_evidence_and_enough_failure():
    """Thin evidence never trips it (9/9 failed is the unreachable rule's job);
    75% of 20 does not either — the floor is 80% of at least 10."""
    vm = _fresh()
    for _ in range(9):
        vm.record(False)
    assert vm.recent()["n"] == 9 and not vm.failing(), "below FLOOR_MIN_CALLS must stay quiet"
    vm = _fresh()
    for _ in range(15):
        vm.record(False)
    for _ in range(5):
        vm.record(True)
    assert vm.recent()["fail_pct"] == 0.75 and not vm.failing()
    vm.record(False)          # ring slides: oldest (a failure) drops, newest is a failure
    assert vm.recent()["n"] == 20 and vm.recent()["fail_pct"] == 0.75


def test_ring_is_bounded_and_forgets_the_past():
    """A vendor that recovers must clear the floor once RING_N good calls have
    pushed the bad ones out — the ring is the last ~20, not since boot."""
    vm = _fresh()
    for _ in range(20):
        vm.record(False)
    assert vm.failing()
    for _ in range(20):
        vm.record(True)
    assert vm.recent() == {"n": 20, "fail": 0, "fail_pct": 0.0}
    assert not vm.failing()
    assert vm.outcomes()["fail"] == 20, "since-boot totals are untouched by the ring"


def test_health_endpoint_reports_vendor_failing(hermetic_app):
    """Route-level: 19 failures + 1 fresh success. Before the floor this read
    "ok" (the success cleared vendor_unreachable). Now degraded/vendor_failing.
    Then 20 successes push the failures out of the ring and health recovers."""
    from fastapi.testclient import TestClient
    app, _ = hermetic_app
    vm = _fresh()
    c = TestClient(app)
    for _ in range(19):
        vm.record(False)
    vm.record(True)
    body = c.get("/api/health").json()
    assert body["vendor_last_ok_min"] == 0
    assert body["status"] == "degraded", "one lucky success must not mask a 95%-failing vendor"
    assert body["degraded_reason"] == "vendor_failing"
    for _ in range(20):
        vm.record(True)
    body = c.get("/api/health").json()
    assert body["status"] == "ok" and "degraded_reason" not in body


def test_unreachable_wins_over_failing_when_both_hold(hermetic_app):
    """20 failures and no success: both rules hold; report the stronger one,
    once — the alert must not read as two outages."""
    from fastapi.testclient import TestClient
    app, _ = hermetic_app
    vm = _fresh()
    for _ in range(20):
        vm.record(False)
    assert vm.failing()
    body = TestClient(app).get("/api/health").json()
    assert body["degraded_reason"] == "vendor_unreachable"


def test_get_call_site_feeds_the_ring(monkeypatch):
    """The call site, not just the helper (again). Ten distinct failing _get
    calls — distinct paths so the cache cannot short-circuit — must fill the
    ring to the floor and trip failing() with no record() call in this test."""
    from app import vendor_meter as vm
    from app import market_routes as M
    importlib.reload(vm)

    class _Resp:
        status_code = 500
        def json(self): return {}

    monkeypatch.setattr(M.requests, "get", lambda *a, **k: _Resp())
    M._cache.clear()
    for i in range(10):
        M._get(f"/__ring_probe_{i}__")
    assert vm.recent()["n"] == 10, "_get must record every outcome into the ring"
    assert vm.failing(), "10/10 failures through the real call site must trip the floor"

    class _OK:
        status_code = 200
        def json(self): return {"x": 1}

    monkeypatch.setattr(M.requests, "get", lambda *a, **k: _OK())
    M._cache.clear()
    M._get("/__ring_probe_ok__")
    assert vm.recent() == {"n": 11, "fail": 10, "fail_pct": 10 / 11}
    assert vm.failing(), "one success after ten failures is still a failing vendor"


# ── DB outage: unmeasured -> degraded ────────────────────────────────────────

def test_fresh_db_with_no_heartbeat_is_ok_not_unmeasured(hermetic_app):
    """The None-vs-exception distinction. A fully-migrated but EMPTY database
    (no heartbeat row, no prices, no sweep) measures None for every signal and
    must stay "ok" — this is a freshly-provisioned deploy, not an outage."""
    from fastapi.testclient import TestClient
    app, _ = hermetic_app
    _fresh()
    body = TestClient(app).get("/api/health").json()
    assert body["status"] == "ok" and "degraded_reason" not in body
    assert body["errors_1h"] == 0
    assert body["error_hours_24h"] == 0        # an empty ring is 0 hours, not null
    assert body["scheduler_beat_min"] is None
    assert body["price_age_days"] is None
    # The session-coverage pair rides the same heartbeat row: absent, not zero.
    # A brand-new database has no sessions to count, and "0 names" would read as
    # a total EOD failure to uptime.yml's ratio gate.
    assert body["eod_names"] is None and body["eod_names_prior"] is None
    assert body["integrity"] is None
    assert body["integrity_age_days"] is None  # no sweep stored → no age, like `integrity`


def test_db_outage_is_degraded_not_ok(tmp_path):
    """The defect: with the DB unreachable every signal query raises, and the
    old code returned {"status":"ok", ...nulls}. Bind get_db to a database that
    cannot be opened at all (sqlite path inside a directory that does not
    exist — the same lazy-connect shape as an RDS outage: nothing fails until
    the first statement) and drive the real route."""
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.database import get_db
    from app.main import app
    _fresh()

    eng = create_engine(f"sqlite:///{tmp_path / 'no_such_dir' / 'down.db'}",
                        connect_args={"check_same_thread": False})
    SL = sessionmaker(bind=eng)

    def _dead():
        s = SL()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _dead
    try:
        r = TestClient(app).get("/api/health")
    finally:
        app.dependency_overrides.pop(get_db, None)
        eng.dispose()

    assert r.status_code == 200          # liveness is still liveness
    body = r.json()
    assert body["status"] == "degraded", "a total DB outage must not read as ok"
    reason = body["degraded_reason"]
    assert reason.startswith("unmeasured:"), reason
    fields = set(reason.split(":", 1)[1].split(","))
    assert fields == {"errors_1h", "error_hours_24h", "scheduler_beat_min",
                      "price_age_days", "integrity"}, fields
    # errors_1h used to read 0 here (errors_last_hour swallowed the DB error) —
    # a false "no errors" during the outage. Under strict it is honestly null.
    # error_hours_24h reads the same ring under the same strict contract.
    assert body["errors_1h"] is None and body["error_hours_24h"] is None
    assert body["scheduler_beat_min"] is None and body["price_age_days"] is None
    # No driver/SQL text leaks into the public payload.
    assert "sqlite" not in reason.lower() and "select" not in reason.lower()


def test_db_outage_with_tables_gone_is_degraded(tmp_path):
    """The exact state the full suite left behind (a shared engine whose tables
    were dropped): connectable, but every table missing. Also degraded — this is
    the case that made the first attempt's route test fail, and the answer it
    got ("degraded") was the right one."""
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.database import get_db
    from app.main import app
    _fresh()

    eng = create_engine(f"sqlite:///{tmp_path / 'empty.db'}",
                        connect_args={"check_same_thread": False})
    SL = sessionmaker(bind=eng)

    def _bare():
        s = SL()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _bare
    try:
        body = TestClient(app).get("/api/health").json()
    finally:
        app.dependency_overrides.pop(get_db, None)
        eng.dispose()
    assert body["status"] == "degraded"
    assert body["degraded_reason"].startswith("unmeasured:")


def test_unmeasured_and_vendor_reasons_compose(tmp_path):
    """Both causes at once must both be visible: the vendor reason first (the
    long-standing contract), the unmeasured list after a ';'."""
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.database import get_db
    from app.main import app
    vm = _fresh()
    for _ in range(3):
        vm.record(False)

    eng = create_engine(f"sqlite:///{tmp_path / 'no_such_dir' / 'down.db'}",
                        connect_args={"check_same_thread": False})
    SL = sessionmaker(bind=eng)

    def _dead():
        s = SL()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _dead
    try:
        body = TestClient(app).get("/api/health").json()
    finally:
        app.dependency_overrides.pop(get_db, None)
        eng.dispose()
    assert body["status"] == "degraded"
    parts = body["degraded_reason"].split(";")
    assert parts[0] == "vendor_unreachable"
    assert parts[1].startswith("unmeasured:")


def test_health_key_set_is_unchanged_by_degrade_reasons(hermetic_app):
    """The FE<->BE contract (tests/contract_keys.json) is key presence; the new
    signals ride inside degraded_reason and add NO steady-state keys — a
    frontend vitest importing the same file must not go red for this."""
    import json, os
    from fastapi.testclient import TestClient
    app, _ = hermetic_app
    _fresh()
    contract = json.load(open(os.path.join(os.path.dirname(__file__), "contract_keys.json")))
    body = TestClient(app).get("/api/health").json()
    assert set(body) == set(contract["/api/health"])
