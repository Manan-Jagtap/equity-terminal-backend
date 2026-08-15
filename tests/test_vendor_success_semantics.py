"""A 200 with an empty body is not a vendor success.

The vendor answers some failure modes with HTTP 200 and an empty or
error-shaped body. Before this, _get counted those as successes (health stayed
green) and cached them — overwriting the last-good payload that the
serve-stale-on-failure design exists to preserve. _get_analyst was worse: not
wired to the meter at all, so its calls burned quota uncounted and its failures
never reached /api/health.
"""
import importlib


def _fresh_meter():
    from app import vendor_meter as vm
    importlib.reload(vm)
    return vm


class _R:
    def __init__(self, status, body):
        self.status_code, self._b = status, body
    def json(self):
        return self._b


def test_empty_200_is_failure_and_preserves_cache(monkeypatch):
    vm = _fresh_meter()
    from app import market_routes as M
    M._cache.clear()

    # 1. a real payload lands and is cached
    monkeypatch.setattr(M.requests, "get", lambda *a, **k: _R(200, {"trending_stocks": {"top_gainers": [1]}}))
    good = M._get("/__sem__")
    assert good["trending_stocks"]["top_gainers"] == [1]
    assert vm.outcomes()["ok"] == 1

    # 2. the vendor dies with 200-{} — must count as FAILURE and serve the
    #    cached payload, not overwrite it (expire the TTL so _get refetches)
    ck = "/__sem__" + str("")
    ts, payload = M._cache[ck]
    M._cache[ck] = (ts - (M.TTL + 1), payload)
    monkeypatch.setattr(M.requests, "get", lambda *a, **k: _R(200, {}))
    stale = M._get("/__sem__")
    assert stale == good, "empty 200 must serve last-good, not overwrite it"
    o = vm.outcomes()
    assert o["fail"] == 1 and o["ok"] == 1

    # 3. error envelope with 200 status: same
    M._cache[ck] = (ts - (M.TTL + 1), payload)
    monkeypatch.setattr(M.requests, "get", lambda *a, **k: _R(200, {"error": "quota"}))
    assert M._get("/__sem__") == good
    assert vm.outcomes()["fail"] == 2


def test_analyst_helper_is_metered(monkeypatch):
    vm = _fresh_meter()
    from app import market_routes as M
    M._cache.clear()
    monkeypatch.setattr(M.requests, "get", lambda *a, **k: _R(200, {"indices": [1]}))
    M._get_analyst("/__an__")
    o = vm.outcomes()
    assert o["ok"] == 1, "_get_analyst success must reach the meter"
    monkeypatch.setattr(M.requests, "get", lambda *a, **k: _R(500, None))
    M._cache.clear()
    M._get_analyst("/__an2__")
    assert vm.outcomes()["fail"] == 1, "_get_analyst failure must reach the meter"
