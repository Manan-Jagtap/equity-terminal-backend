"""A total vendor outage must not report as healthy.

14 Aug 2026: every IndianAPI call failed for 5+ hours while /api/health returned
{"status":"ok","errors_1h":0}. _get() serves its last good payload on upstream
failure, so no exception ever reached the error log — the reporting was honest
about errors and silent about reachability. These tests pin the distinction.
"""
import importlib


def _fresh():
    from app import vendor_meter as vm
    importlib.reload(vm)
    return vm


def test_no_calls_is_not_degraded():
    """A container that made no vendor calls stays ok — no evidence is not bad
    evidence, and this is the normal state for a quiet scheduler."""
    vm = _fresh()
    o = vm.outcomes()
    assert o == {"ok": 0, "fail": 0, "last_ok_min": None, "last_fail_min": None}
    assert not (o["fail"] > 0 and (o["last_ok_min"] is None or o["last_ok_min"] >= 30))


def test_failures_with_no_success_degrade():
    """The exact 14 Aug shape: calls happening, all failing, none succeeding."""
    vm = _fresh()
    for _ in range(5):
        vm.record(False)
    o = vm.outcomes()
    assert o["fail"] == 5 and o["ok"] == 0 and o["last_ok_min"] is None
    assert o["fail"] > 0 and (o["last_ok_min"] is None or o["last_ok_min"] >= 30)


def test_recent_success_is_not_degraded():
    """Flaky calls alongside a recent success must NOT page anyone."""
    vm = _fresh()
    vm.record(False)
    vm.record(True)
    o = vm.outcomes()
    assert o["last_ok_min"] == 0
    assert not (o["fail"] > 0 and (o["last_ok_min"] is None or o["last_ok_min"] >= 30))


def test_health_endpoint_reports_degraded(monkeypatch):
    """End-to-end through the real route, not just the helper — the earlier
    watchlist and backtest lessons: a helper-only test passes with the call site
    deleted."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app import vendor_meter as vm
    importlib.reload(vm)
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
