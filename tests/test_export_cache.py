"""GET /api/export/screener.xlsx must not rebuild the universe per request.

The route is UNAUTHENTICATED and was uncached: every hit walked ~1000 companies,
built an openpyxl workbook in memory and serialised it. On a t3.micro that is a
trivially repeatable way to pin CPU and memory — no credentials, no rate limit,
just refresh.
"""
import time

import pytest

import app.export_routes as ex


@pytest.fixture(autouse=True)
def _clear():
    ex._SCREENER_XLSX_CACHE.update(at=0.0, body=None)
    yield
    ex._SCREENER_XLSX_CACHE.update(at=0.0, body=None)


def test_second_request_does_not_rebuild(monkeypatch):
    builds = {"n": 0}
    real = ex.Workbook

    class Counting(real):
        def __init__(self, *a, **k):
            builds["n"] += 1
            super().__init__(*a, **k)

    monkeypatch.setattr(ex, "Workbook", Counting)

    class _DB:
        def query(self, *a, **k): return self
        def all(self): return []
        def order_by(self, *a, **k): return self
        def filter(self, *a, **k): return self
        def rollback(self): pass

    db = _DB()
    ex.export_screener(db=db)
    ex.export_screener(db=db)
    ex.export_screener(db=db)
    assert builds["n"] == 1, f"rebuilt {builds['n']}x for 3 requests — cache not applied"


def test_cache_expires(monkeypatch):
    """A stale workbook must not outlive the TTL — the data does change on a
    scheduler recompute, just not per request."""
    ex._SCREENER_XLSX_CACHE.update(at=time.time() - (ex._SCREENER_XLSX_TTL + 5), body=b"stale")
    assert (time.time() - ex._SCREENER_XLSX_CACHE["at"]) > ex._SCREENER_XLSX_TTL


def test_a_fresh_entry_is_served(monkeypatch):
    ex._SCREENER_XLSX_CACHE.update(at=time.time(), body=b"PK-fake-xlsx")
    class _DB:
        def query(self, *a, **k): raise AssertionError("must not touch the DB on a cache hit")
    resp = ex.export_screener(db=_DB())
    assert resp.body == b"PK-fake-xlsx"
    assert "screener.xlsx" in resp.headers["Content-Disposition"]
