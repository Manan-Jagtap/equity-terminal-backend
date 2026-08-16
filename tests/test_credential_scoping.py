"""A paid credential goes to the vendor host and nowhere else.

The logo route fetches from a public CDN (www.livemint.com) before falling back
to the vendor. It used to attach the IndianAPI key to BOTH requests, handing the
paid credential to an unrelated third party on every cache miss, and it ticked
the vendor quota meter for the CDN fetch too — inflating our own tally against a
request the vendor never saw.
"""
import os, sys, inspect, re
from types import SimpleNamespace
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi import HTTPException
from app import logo_routes, dhan_routes
from app.admin_routes import require_admin


def test_key_is_only_attached_to_the_vendor_host():
    src = inspect.getsource(logo_routes)
    assert "_vendor = url.startswith(BASE)" in src, "host check missing"
    assert 'hdrs = {"X-API-Key": KEY, "x-api-key": KEY} if _vendor else {}' in src, \
        "the credential must be conditional on the vendor host"
    # the unconditional form must be gone
    assert 'requests.get(url, headers={"X-API-Key": KEY' not in src, \
        "credential still sent unconditionally"


def test_vendor_meter_only_ticks_for_vendor_calls():
    src = inspect.getsource(logo_routes)
    m = re.search(r"if _vendor:\s*\n\s*from app import vendor_meter; vendor_meter\.tick\(\)", src)
    assert m, "the CDN fetch must not tick the vendor quota meter"


def test_livemint_is_still_tried_first():
    """The fallback order is deliberate — the fix must not change behaviour,
    only who receives the credential."""
    src = inspect.getsource(logo_routes)
    i_cdn = src.index("livemint.com")
    i_vendor = src.index('f"{BASE}/logo/')
    assert i_cdn < i_vendor, "CDN-first ordering should be preserved"


class _Q:
    """Just enough of a SQLAlchemy query for logo(): one row, always found."""
    def __init__(self, obj):
        self._o = obj

    def filter_by(self, **kw):
        return self

    def first(self):
        return self._o


class _DB:
    def __init__(self, ticker_id):
        self._tid = ticker_id

    def query(self, model):
        from app import models
        if model is models.Company:
            return _Q(SimpleNamespace(id=1, ticker="TCS"))
        return _Q(SimpleNamespace(ticker_id=self._tid))


def test_dead_vendor_logo_leg_is_not_called(monkeypatch):
    """The production host dropped /logo/{id} on 13 Jul 2026. Until it was
    short-circuited, every CDN miss still tried it — a guaranteed failure behind
    a 15s timeout, stalling a worker thread and ticking the paid quota before
    the frontend got its 404 and drew the neutral tile."""
    logo_routes._CACHE.clear()
    seen = []

    def _spy(url, *a, **k):
        seen.append(url)
        return SimpleNamespace(status_code=404, headers={}, content=b"")

    monkeypatch.setattr(logo_routes.requests, "get", _spy)
    with pytest.raises(HTTPException):
        logo_routes.logo("TCS", db=_DB("1234"))
    assert seen == ["https://www.livemint.com/lm-img/markets/logo/1234.png"], \
        f"only the CDN may be tried; the vendor leg is dead — got {seen}"


def _deps(fn):
    return [p.default.dependency for p in inspect.signature(fn).parameters.values()
            if p.default is not inspect.Parameter.empty and hasattr(p.default, "dependency")]


def test_dhan_status_is_admin_only():
    """It makes several LIVE broker calls per request; anonymous callers must
    not be able to spend the owner's broker quota or stall worker threads."""
    assert require_admin in _deps(dhan_routes.dhan_status), \
        "/api/dhan/status must be admin-gated"
