"""DATA-12, second half: an OFF-PLAN vendor endpoint must never WRITE.

On 24 Jul 2026 the vendor took two endpoints off-plan. /documents answers 404;
/historical_stats answers HTTP 200 with the body {"info": "Not a valid
script_code"} for every name. The merge shipped for DATA-12 protects a key that
is ABSENT from the fresh blob — but that body is a NON-EMPTY dict, so
_ratios/_growth returned it as a result, _build_insight's `if v` filter kept it,
and merged.update() wrote the envelope OVER the stored last-good ratios and
growth. Silent corruption of stored fundamentals, once per name per refresh:
/api/operations dropped the name entirely (operational_snapshot finds no metric
inside {"info": ...}), the one-pager shipped the envelope as the historical CAGR
block, and coverage's has_ratios still answered True.

Two independent guards, tested separately because both must hold:
  * off-plan endpoints are short-circuited — no HTTP, no quota (the spend half);
  * an error-shaped body is NO DATA even once the endpoint is back on plan
    (the correctness half — this is what actually stops the overwrite).
"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

from app import models
from app.database import Base, SessionLocal, engine
from app.ingest import indianapi_ingester as ing

_TICKER = "OFFPLANT"
_STORED = {"ratios": {"ROCE %": {"Mar 2024": 58.0, "Mar 2025": 62.0}},
           "growth": {"Compounded Sales Growth": {"3 Years:": "6%"}}}


class _R:
    def __init__(self, status=200, body=None):
        self.status_code, self._b = status, body
        self.text, self.headers, self.content = "", {}, b""

    def json(self):
        return self._b


def _vendor(calls):
    """The vendor as it has actually behaved since 24 Jul 2026."""
    def _get(url, **kw):
        calls.append(url)
        if url.endswith("/historical_stats"):
            return _R(200, {"info": "Not a valid script_code"})
        if url.endswith("/documents"):
            return _R(404, None)
        return _R(200, None)          # every other insight endpoint: nothing here
    return _get


@pytest.fixture()
def db():
    Base.metadata.create_all(engine)
    s = SessionLocal()
    yield s
    s.rollback()
    for co in s.query(models.Company).filter_by(ticker=_TICKER).all():
        s.query(models.CompanyInsight).filter_by(company_id=co.id).delete()
        s.delete(co)
    s.commit()
    s.close()


def test_offplan_endpoints_spend_nothing(monkeypatch):
    """The quota half: an endpoint that cannot answer must not be asked."""
    from app import vendor_meter as vm
    calls = []
    monkeypatch.setattr(ing, "KEY", "realkey")
    monkeypatch.setattr(ing.requests, "get", _vendor(calls))
    before = vm.total()

    assert ing._ratios("TCS") is None
    assert ing._growth("TCS") is None
    assert ing._results_snapshot("TCS") is None
    assert ing._documents("TCS") is None

    assert calls == [], f"an off-plan endpoint was still called: {calls}"
    assert vm.total() == before, "a call that was never made must not burn quota"


def test_error_shaped_body_is_no_data_even_when_the_endpoint_returns(monkeypatch):
    """The correctness half — the guard that survives the flag being flipped
    back on. A 200 whose body is only {"info": ...} is the vendor declining."""
    calls = []
    monkeypatch.setattr(ing, "KEY", "realkey")
    monkeypatch.setattr(ing, "_HISTORICAL_STATS_ON_PLAN", True)
    monkeypatch.setattr(ing.requests, "get", _vendor(calls))

    assert ing._ratios("TCS") is None
    assert ing._growth("TCS") is None
    assert calls, "the endpoint is on plan in this test — it must be called"


def test_a_real_series_still_flows_through(monkeypatch):
    """The guard must not eat a legitimate payload."""
    monkeypatch.setattr(ing, "KEY", "realkey")
    monkeypatch.setattr(ing, "_HISTORICAL_STATS_ON_PLAN", True)
    monkeypatch.setattr(ing.requests, "get",
                        lambda *a, **k: _R(200, {"ROCE %": {"Mar 2025": 20.0}}))
    assert ing._ratios("TCS") == {"ROCE %": {"Mar 2025": 20.0}}


def test_refresh_leaves_stored_ratios_and_growth_untouched(db, monkeypatch):
    """The write site itself, which is where the corruption happened. Before the
    fix these assertions found {"info": "Not a valid script_code"} where twelve
    years of ROCE and the sales CAGR had been."""
    co = models.Company(ticker=_TICKER, name="Off Plan Test Co",
                        type="nonfinancial", sector="Testing",
                        shares_outstanding=10.0)
    db.add(co)
    db.commit()
    db.add(models.CompanyInsight(company_id=co.id, ticker_id="S0000001",
                                 data=copy.deepcopy(_STORED)))
    db.commit()

    monkeypatch.setattr(ing, "KEY", "realkey")
    monkeypatch.setattr(ing.requests, "get", _vendor([]))
    ing._build_insight(db, co, {"companyName": "Off Plan Test Co"})
    db.commit()

    got = db.query(models.CompanyInsight).filter_by(company_id=co.id).first().data
    assert got["ratios"] == _STORED["ratios"], \
        "stored ratios were overwritten by the vendor's error envelope"
    assert got["growth"] == _STORED["growth"], \
        "stored growth was overwritten by the vendor's error envelope"
