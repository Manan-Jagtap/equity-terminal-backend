"""The mutual-fund routes must not spend vendor quota without a budget check.

Every route in app/mf_routes.py is UNAUTHENTICATED and takes a caller-chosen
`name` / `q` / `id`, which is also the in-memory cache key — so each distinct
value is a guaranteed cache miss and a fresh IndianAPI call. `vendor_meter.tick()`
recorded that spend, but nothing refused it: an unauthenticated script iterating
junk names could drain the month's quota. The guard (app/api_budget.py) already
existed and was used by profile_routes and manager_engine; it simply was not
reachable from a module-level `_get` with no request-scoped session.

These tests pin the four behaviours that matter, including that the guard fails
OPEN — a budget check that errors must never take the fund board down.
"""
import time

import pytest

from app import mf_routes


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Fake the vendor transport and count outbound calls."""
    calls = {"n": 0}

    class _Resp:
        status_code = 200

        def json(self):
            return {"ok": True}

    def _fake_get(url, **kw):
        calls["n"] += 1
        return _Resp()

    monkeypatch.setattr(mf_routes.requests, "get", _fake_get)
    monkeypatch.setattr(mf_routes, "KEY", "test-key")
    mf_routes._cache.clear()
    yield calls
    mf_routes._cache.clear()
    mf_routes._BUDGET_CK.update(at=0.0, ok=True)


def _pin_budget(ok: bool):
    """Pin the cached guard verdict so the test never touches a real DB."""
    mf_routes._BUDGET_CK.update(at=time.time(), ok=ok)


def test_spends_when_budget_remains(_isolate):
    _pin_budget(True)
    out = mf_routes._get("/mutual_fund_search", {"query": "hdfc"})
    assert _isolate["n"] == 1
    assert out is not None


def test_refuses_the_vendor_call_when_budget_is_spent(_isolate):
    _pin_budget(False)
    mf_routes._get("/mutual_fund_search", {"query": "anything"})
    assert _isolate["n"] == 0, "an exhausted budget must not reach the vendor"


def test_serves_last_known_good_instead_of_spending(_isolate):
    _pin_budget(True)
    mf_routes._get("/x", {"q": "cached"})          # warm the cache (1 call)
    assert _isolate["n"] == 1
    _pin_budget(False)
    # ttl=0 forces a cache MISS, so only the guard can prevent a second call
    stale = mf_routes._get("/x", {"q": "cached"}, ttl=0)
    assert _isolate["n"] == 1, "must not spend past the ceiling on a TTL miss"
    assert stale is not None, "should degrade to last-known-good, not to None"


def test_guard_fails_open_when_the_database_is_unreachable(monkeypatch):
    """A broken guard must not become an outage."""
    import app.database as dbmod

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(dbmod, "SessionLocal", _boom)
    mf_routes._BUDGET_CK.update(at=0.0, ok=True)   # force a real re-check
    assert mf_routes._budget_ok() is True


def test_verdict_is_cached_so_the_guard_costs_one_query_per_minute(monkeypatch):
    """The guard must not add a DB round-trip to every vendor call."""
    hits = {"n": 0}
    import app.database as dbmod

    class _DB:
        def close(self):
            pass

    def _session():
        hits["n"] += 1
        return _DB()

    monkeypatch.setattr(dbmod, "SessionLocal", _session)
    monkeypatch.setattr("app.api_budget.would_exceed", lambda db, n: False)
    mf_routes._BUDGET_CK.update(at=0.0, ok=True)
    for _ in range(5):
        mf_routes._budget_ok()
    assert hits["n"] == 1, f"expected one session for five checks, got {hits['n']}"
