"""Every IndianAPI call site reports its OUTCOME, not just its spend.

#140/#141 wired vendor_meter.record() into market_routes._get/_get_analyst and
built /api/health's "vendor_unreachable" / "vendor_failing" on top of it. Every
OTHER vendor call site — mutual funds, IPOs, news, profiles, intraday, the
admin usage probe, the bulk ingester — still only tick()ed: quota burn was
counted, failures never reached health. And three of them (/ipo/v2, /ipo/{id},
the /news fallback) did not even tick.

These tests drive each REAL call site with the transport faked — a helper-level
test would pass with the wiring missing (the lesson recorded in
test_health_surfaces_vendor_failure). They also pin vendor_meter.payload_ok,
the shared judgement: None / envelope → failure; empty → the call site's
`empty_ok`; {"info": ...} → success (an off-plan endpoint, not a dead vendor).
"""
import importlib

import pytest


def _fresh():
    from app import vendor_meter as vm
    importlib.reload(vm)
    return vm


class _R:
    def __init__(self, status=200, body=None):
        self.status_code, self._b = status, body
        self.text = ""
        self.headers = {"content-type": "application/json"}
        self.content = b""

    def json(self):
        return self._b


# ── payload_ok: the shared judgement ─────────────────────────────────────────

@pytest.mark.parametrize("body,empty_ok,expect", [
    (None, False, False), (None, True, False),               # no answer at all
    ({}, False, False), ({}, True, True),                    # empty: per site
    ([], False, False), ([], True, True),
    ("", False, False), ("", True, True),
    ({"error": "quota"}, True, False),                       # envelope, even where empty is ok
    ({"message": "x", "detail": "y"}, True, False),
    ([{"error": "select a valid statement"}, 500], True, False),   # observed insight shape
    ([{"error": "x"}], True, False),
    ([{"a": 1}, 502], True, False),
    ({"info": "Not a valid script_code"}, False, True),      # off-plan, not down (DATA-12)
    ({"companyName": "TCS"}, False, True),
    ([{"date": "2026-01-01", "nav": 10.0}], False, True),
    ({"message": "ok", "data": [1]}, False, True),           # a message key beside real data
])
def test_payload_ok_table(body, empty_ok, expect):
    vm = _fresh()
    assert vm.payload_ok(body, empty_ok=empty_ok) is expect


# ── mutual funds ─────────────────────────────────────────────────────────────

def test_mf_get_records_and_stops_caching_envelopes(monkeypatch):
    import time
    vm = _fresh()
    from app import mf_routes as MF
    monkeypatch.setattr(MF, "KEY", "k")
    MF._BUDGET_CK.update(at=time.time(), ok=True)      # pin the guard: no DB
    MF._cache.clear()

    monkeypatch.setattr(MF.requests, "get",
                        lambda *a, **k: _R(200, {"Equity": {"Large": [{"fund_name": "x"}]}}))
    good = MF._get("/__mf_cat__", empty_ok=False)
    assert good and vm.outcomes()["ok"] == 1

    # an error envelope with a 200 is a FAILURE and must not overwrite last-good
    monkeypatch.setattr(MF.requests, "get", lambda *a, **k: _R(200, {"error": "quota"}))
    assert MF._get("/__mf_cat__", ttl=0, empty_ok=False) == good
    assert vm.outcomes()["fail"] == 1

    # a search that matches nothing is the vendor answering (caller-chosen key)
    monkeypatch.setattr(MF.requests, "get", lambda *a, **k: _R(200, []))
    assert MF._get("/__mf_search__", {"query": "zzz"}) == []
    assert vm.outcomes()["ok"] == 2

    # ...but the catalog is never legitimately empty
    monkeypatch.setattr(MF.requests, "get", lambda *a, **k: _R(200, {}))
    MF._get("/__mf_cat2__", empty_ok=False)
    assert vm.outcomes()["fail"] == 2

    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(MF.requests, "get", boom)
    MF._get("/__mf_boom__")
    assert vm.outcomes()["fail"] == 3
    MF._cache.clear()
    MF._BUDGET_CK.update(at=0.0, ok=True)


def test_mf_catalog_route_declares_itself_never_empty(monkeypatch):
    """catalog() is the one mf feed that must pass empty_ok=False."""
    import time
    vm = _fresh()
    from app import mf_routes as MF
    monkeypatch.setattr(MF, "KEY", "k")
    MF._BUDGET_CK.update(at=time.time(), ok=True)
    MF._cache.clear()
    monkeypatch.setattr(MF.requests, "get", lambda *a, **k: _R(200, {}))
    out = MF.catalog()
    assert out["available"] is False
    assert vm.outcomes()["fail"] == 1, "an empty catalog is upstream trouble"
    MF._cache.clear()
    MF._BUDGET_CK.update(at=0.0, ok=True)


# ── profiles ─────────────────────────────────────────────────────────────────

def test_profile_get_records(monkeypatch):
    vm = _fresh()
    from app import profile_routes as P
    monkeypatch.setattr(P, "KEY", "k")
    P._cache.clear()

    monkeypatch.setattr(P.requests, "get", lambda *a, **k: _R(200, {"companyProfile": {"x": 1}}))
    good = P._get("/stock", {"name": "__P__"}, empty_ok=False)
    assert vm.outcomes()["ok"] == 1

    monkeypatch.setattr(P.requests, "get", lambda *a, **k: _R(200, {"error": "x"}))
    assert P._get("/stock", {"name": "__P__"}, ttl=0, empty_ok=False) == good, \
        "an envelope must not be cached over the last-good /stock payload"
    assert vm.outcomes()["fail"] == 1

    monkeypatch.setattr(P.requests, "get", lambda *a, **k: _R(200, {}))
    P._get("/stock", {"name": "__P2__"}, empty_ok=False)
    assert vm.outcomes()["fail"] == 2, "/stock never legitimately answers a tracked ticker with {}"

    monkeypatch.setattr(P.requests, "get", lambda *a, **k: _R(200, []))
    assert P._get("/credit_ratings", {"stock_name": "__P__"}) == []
    assert vm.outcomes()["ok"] == 2, "a small name with no rated debt is not a dead vendor"

    monkeypatch.setattr(P.requests, "get", lambda *a, **k: _R(200, {"info": "Not a valid script_code"}))
    P._get("/historical_stats", {"stock_name": "__P__", "stats": "ratios"})
    assert vm.outcomes()["ok"] == 3, "an off-plan endpoint answering 200 is upstream answering"

    monkeypatch.setattr(P.requests, "get", lambda *a, **k: _R(503, None))
    P._get("/concalls", {"stock_name": "__P__"})
    assert vm.outcomes()["fail"] == 3
    P._cache.clear()


# ── IPOs ─────────────────────────────────────────────────────────────────────

def test_ipo_board_meters_and_records_all_five_calls(monkeypatch):
    vm = _fresh()
    from app import ipo_routes as I
    monkeypatch.setattr(I, "KEY", "k")
    I._cache.update(ts=0.0, data=None)

    def fake(url, **kw):
        if url.endswith("/ipo"):
            return _R(200, {"upcoming": [{"name": "A", "symbol": "A"}],
                            "active": [], "closed": [], "listed": []})
        return _R(200, [])                       # /ipo/v2 buckets: legitimately empty
    monkeypatch.setattr(I.requests, "get", fake)
    out = I.ipo_board()
    assert out["available"] is True
    assert vm.pending() == 5, "base + four v2 buckets: every one is spend (v2 was unmetered)"
    o = vm.outcomes()
    assert (o["ok"], o["fail"]) == (5, 0)

    # total outage: every call 5xx → five failures, last-good served
    I._cache["ts"] = 0.0                         # expire the TTL, keep last-good
    monkeypatch.setattr(I.requests, "get", lambda *a, **k: _R(502, None))
    assert I.ipo_board() == out
    assert vm.outcomes()["fail"] == 5

    # empty base board is upstream trouble, even at 200
    I._cache["ts"] = 0.0
    monkeypatch.setattr(I.requests, "get", lambda *a, **k: _R(200, {}))
    assert I.ipo_board() == out
    assert vm.outcomes()["fail"] == 6           # +1 base; the four v2 {} are ok
    I._cache.update(ts=0.0, data=None)


def test_ipo_detail_is_metered_and_recorded(monkeypatch):
    vm = _fresh()
    from app import ipo_routes as I
    monkeypatch.setattr(I.requests, "get", lambda *a, **k: _R(200, {"data": {"name": "X"}}))
    assert I.ipo_detail("123") == {"name": "X"}
    assert vm.pending() == 1 and vm.outcomes()["ok"] == 1
    monkeypatch.setattr(I.requests, "get", lambda *a, **k: _R(200, {}))
    assert I.ipo_detail("junk") == {}
    assert vm.outcomes()["ok"] == 2, "a junk id (caller-chosen) answering empty is not a failure"
    monkeypatch.setattr(I.requests, "get", lambda *a, **k: _R(500, None))
    assert I.ipo_detail("123") == {"available": False}
    assert vm.outcomes()["fail"] == 1


# ── news ─────────────────────────────────────────────────────────────────────

def test_news_sites_record(monkeypatch):
    vm = _fresh()
    from app import news_routes as N
    monkeypatch.setenv("INDIANAPI_KEY", "k")

    monkeypatch.setattr(N.requests, "get", lambda *a, **k: _R(200, {
        "recentNews": [{"headline": "H", "date": "12 Jul 2026", "url": "u"}]}))
    items, err = N._indianapi_news("TCS", "Tata Consultancy Services Ltd")
    assert items and err is None
    assert vm.outcomes()["ok"] == 1                       # first candidate hit, loop stopped

    monkeypatch.setattr(N.requests, "get", lambda *a, **k: _R(200, {}))
    items, err = N._indianapi_news("XX", "Some Name Ltd")  # 3 candidates, all miss
    assert items == [] and err == "no_results"
    o = vm.outcomes()
    assert (o["ok"], o["fail"]) == (4, 0), "name-form misses are not vendor failures"

    monkeypatch.setattr(N.requests, "get", lambda *a, **k: _R(401, None))
    N._indianapi_news("XX", "")
    assert vm.outcomes()["fail"] == 1

    # the /news fallback: was unmetered
    monkeypatch.setattr(N.requests, "get", lambda *a, **k: _R(200, [{"title": "t", "summary": "s"}]))
    p0 = vm.pending()
    N._market_news_for("XX", "Some Name")
    assert vm.pending() == p0 + 1, "the /news fallback must tick the meter"
    assert vm.outcomes()["ok"] == 5
    monkeypatch.setattr(N.requests, "get", lambda *a, **k: _R(200, []))
    N._market_news_for("XX", "Some Name")
    assert vm.outcomes()["fail"] == 2, "the general market feed is never legitimately empty"
    monkeypatch.setattr(N.requests, "get", lambda *a, **k: _R(503, None))
    assert N._market_news_for("XX", "Some Name") == ([], "http_503")
    assert vm.outcomes()["fail"] == 3


# ── intraday ─────────────────────────────────────────────────────────────────

def test_intraday_records_on_the_routes_own_boundary(monkeypatch, tmp_path):
    vm = _fresh()
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.database import Base
    from app import models, api_budget
    from app import intraday_routes as IR

    eng = create_engine(f"sqlite:///{tmp_path / 'i.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    co = models.Company(ticker="ZZINTRA", name="ZZ", type="nonfinancial", sector="X",
                        shares_outstanding=1.0)
    s.add(co); s.flush()
    s.add(models.CompanyInsight(company_id=co.id, ticker_id="S0001", data={}))
    s.commit()
    monkeypatch.setattr(IR, "KEY", "k")
    monkeypatch.setattr(api_budget, "would_exceed", lambda *a, **k: False)
    IR._cache.clear()
    try:
        # healthy closed market: a row with no ticks — NOT a failure
        monkeypatch.setattr(IR.requests, "post",
                            lambda *a, **k: _R(200, [{"values": [], "returnValue": 10, "netChange": 0}]))
        out = IR.intraday("ZZINTRA", s)
        assert "reason" not in out and out["available"] is False
        assert vm.outcomes()["ok"] == 1

        IR._cache.clear()
        monkeypatch.setattr(IR.requests, "post", lambda *a, **k: _R(200, []))
        assert IR.intraday("ZZINTRA", s)["reason"] == "vendor_error"
        assert vm.outcomes()["fail"] == 1, "what the route calls vendor_error, the meter calls a failure"

        IR._cache.clear()
        monkeypatch.setattr(IR.requests, "post", lambda *a, **k: _R(500, None))
        assert IR.intraday("ZZINTRA", s)["reason"] == "vendor_error"
        assert vm.outcomes()["fail"] == 2
        assert vm.pending() == 3
    finally:
        IR._cache.clear()
        s.close(); eng.dispose()


# ── admin usage probe ────────────────────────────────────────────────────────

def test_admin_usage_probe_records(monkeypatch):
    vm = _fresh()
    from app import admin_routes as A
    monkeypatch.setenv("INDIANAPI_KEY", "k")
    monkeypatch.setattr("app.api_budget.month_usage", lambda *a, **k: 0)   # no DB query
    monkeypatch.setattr("requests.get", lambda *a, **k: _R(200, {"total_requests": 5, "hard_limit": 100}))
    out = A.api_usage(_admin=None)
    assert out["vendor"]["hard_limit"] == 100 and vm.outcomes()["ok"] == 1
    monkeypatch.setattr("requests.get", lambda *a, **k: _R(403, None))
    assert A.api_usage(_admin=None)["vendor"] is None
    assert vm.outcomes()["fail"] == 1


# ── bulk ingester ────────────────────────────────────────────────────────────

def test_ingester_get_records_per_attempt_without_double_ticking(monkeypatch):
    vm = _fresh()
    from app.ingest import indianapi_ingester as ing
    monkeypatch.setattr(ing, "KEY", "realkey")
    monkeypatch.setattr(ing.time, "sleep", lambda *_: None)

    seq = iter([_R(503, None), _R(200, {"companyName": "TCS"})])
    monkeypatch.setattr(ing.requests, "get", lambda *a, **k: next(seq))
    assert ing._get("/stock", {"name": "TCS"})["companyName"] == "TCS"
    o = vm.outcomes()
    assert (o["ok"], o["fail"]) == (1, 1), "each attempt is one call the vendor did or did not answer"
    assert vm.pending() == 2, "one tick per attempt, as before — record() adds no spend"

    monkeypatch.setattr(ing.requests, "get", lambda *a, **k: _R(403, None))
    with pytest.raises(RuntimeError):
        ing._get("/stock", {"name": "TCS"})            # non-retryable: raises AND records
    assert vm.outcomes()["fail"] == 2

    def boom(*a, **k):
        raise ing.requests.exceptions.ConnectionError("x")
    monkeypatch.setattr(ing.requests, "get", boom)
    with pytest.raises(RuntimeError):
        ing._get("/stock", {"name": "TCS"}, retries=2)
    assert vm.outcomes()["fail"] == 4

    monkeypatch.setattr(ing.requests, "get", lambda *a, **k: _R(200, {}))
    assert ing._get("/stock", {"name": "TCS"}) == {}   # return value unchanged...
    assert vm.outcomes()["fail"] == 5                    # ...but a 200-{} /stock is not a success


def test_ingester_get_safe_records(monkeypatch):
    vm = _fresh()
    from app.ingest import indianapi_ingester as ing
    monkeypatch.setattr(ing, "KEY", "realkey")

    monkeypatch.setattr(ing.requests, "get", lambda *a, **k: _R(200, {"ROCE %": {"Mar 2025": 20}}))
    assert ing._get_safe("/historical_stats", {"stock_name": "TCS", "stats": "ratios"})
    assert vm.outcomes()["ok"] == 1 and vm.pending() == 1

    monkeypatch.setattr(ing.requests, "get", lambda *a, **k: _R(200, [{"error": "x"}, 500]))
    assert ing._get_safe("/stock_forecasts", {}) is None
    assert vm.outcomes()["fail"] == 1

    monkeypatch.setattr(ing.requests, "get", lambda *a, **k: _R(404, None))     # /documents since 24 Jul
    assert ing._get_safe("/documents", {"stock_name": "TCS"}) is None
    assert vm.outcomes()["fail"] == 2

    monkeypatch.setattr(ing.requests, "get", lambda *a, **k: _R(200, {"info": "Not a valid script_code"}))
    ing._get_safe("/historical_stats", {"stock_name": "TCS", "stats": "ratios"})
    assert vm.outcomes()["ok"] == 2, "an off-plan 200 is upstream answering"

    monkeypatch.setattr(ing.requests, "get", lambda *a, **k: _R(200, []))
    ing._get_safe("/stock_forecasts", {})
    assert vm.outcomes()["ok"] == 3, "a name with nothing to report is not a dead vendor"

    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(ing.requests, "get", boom)
    assert ing._get_safe("/statement", {}) is None
    assert vm.outcomes()["fail"] == 3


# ── deliberately NOT recorded ────────────────────────────────────────────────

def test_news_red_flags_dead_endpoint_does_not_poison_the_ring(monkeypatch):
    """/company_news does not exist on the production host (news_routes,
    12 Jul 2026). Its inevitable non-200 is spend, not evidence: recording it
    would read as a failing vendor on every evidence rebuild in the web process."""
    vm = _fresh()
    from app import manager_engine as ME
    monkeypatch.setenv("INDIANAPI_KEY", "k")
    monkeypatch.setattr("requests.get", lambda *a, **k: _R(404, None))
    assert ME.news_red_flags("TCS") == []
    assert vm.pending() == 1, "spend is still counted"
    assert vm.outcomes() == {"ok": 0, "fail": 0, "last_ok_min": None, "last_fail_min": None}


def test_ipo_board_without_a_key_makes_no_call_and_does_not_crash(monkeypatch):
    """No key → no vendor call → nothing to record; the empty shell, not a NameError."""
    vm = _fresh()
    from app import ipo_routes as I
    monkeypatch.setattr(I, "KEY", "")
    I._cache.update(ts=0.0, data=None)
    out = I.ipo_board()
    assert out["available"] is False and vm.pending() == 0
    assert (vm.outcomes()["ok"], vm.outcomes()["fail"]) == (0, 0)
