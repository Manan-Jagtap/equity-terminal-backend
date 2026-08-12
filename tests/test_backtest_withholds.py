"""The public backtest must not republish withheld point estimates —
and must not damage the ledger doing it.

/api/backtest is UNAUTHENTICATED and was serving 119 rows across 98 tickers
carrying the exact figures the rest of the product refuses to show, including
ADANIGREEN's 64.36 while /api/companies/ADANIGREEN reports intrinsic: null.

The tests below pin BOTH halves of the judgement:
  - the two point estimates are withheld for suppressed names, and
  - everything the public track record consists of survives untouched — the
    verdict, both dates, both prices, the realised return. Per the ledger
    doctrine, rows are never dropped and outcomes are never rewritten.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.backtest_routes import _withhold_point_estimates


@pytest.fixture()
def db():
    eng = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    co_sup = models.Company(ticker="SUPPRESSED", name="Sup Co", type="nonfinancial",
                            sector="Software & Programming", shares_outstanding=10.0)
    co_ok = models.Company(ticker="CLEAN", name="Clean Co", type="nonfinancial",
                           sector="Software & Programming", shares_outstanding=10.0)
    s.add_all([co_sup, co_ok]); s.commit()
    s.add_all([
        models.Valuation(company_id=co_sup.id, gate_state="high_dispersion",
                         intrinsic=64.36, mos=-0.95, verdict="AVOID"),
        models.Valuation(company_id=co_ok.id, gate_state="clean",
                         intrinsic=2729.0, mos=0.12, verdict="ACCUMULATE"),
    ])
    s.commit()
    yield s
    s.close()


def _payload():
    return {"total_calls": 2, "calls": [
        {"ticker": "SUPPRESSED", "verdict": "AVOID", "start_date": "2026-06-01",
         "start_price": 1000.0, "end_date": "2026-08-01", "end_price": 900.0,
         "ret": -0.10, "total_ret": -0.09, "div_ret": 0.01, "days": 61, "open": False,
         "intrinsic_at_call": 64.36, "mos_at_call": -0.95, "sector": "IT_SERVICES"},
        {"ticker": "CLEAN", "verdict": "ACCUMULATE", "start_date": "2026-06-01",
         "start_price": 100.0, "end_date": "2026-08-01", "end_price": 112.0,
         "ret": 0.12, "total_ret": 0.12, "div_ret": 0.0, "days": 61, "open": False,
         "intrinsic_at_call": 2729.0, "mos_at_call": 0.12, "sector": "IT_SERVICES"},
    ]}


def test_point_estimates_withheld_for_suppressed_names(db):
    out = _withhold_point_estimates(_payload(), db)
    row = next(r for r in out["calls"] if r["ticker"] == "SUPPRESSED")
    assert row["intrinsic_at_call"] is None
    assert row["mos_at_call"] is None
    assert row["value_withheld"] is True


def test_the_ledger_itself_is_untouched(db):
    """The doctrine: never drop a row, never rewrite an outcome."""
    before = _payload()
    out = _withhold_point_estimates(_payload(), db)
    assert len(out["calls"]) == len(before["calls"]) == 2      # no row dropped
    row = next(r for r in out["calls"] if r["ticker"] == "SUPPRESSED")
    keep = ("verdict", "start_date", "start_price", "end_date", "end_price",
            "ret", "total_ret", "div_ret", "days", "open", "sector")
    orig = next(r for r in before["calls"] if r["ticker"] == "SUPPRESSED")
    for k in keep:
        assert row[k] == orig[k], f"{k} was altered — that is the track record"


def test_a_clean_name_keeps_its_figures(db):
    out = _withhold_point_estimates(_payload(), db)
    row = next(r for r in out["calls"] if r["ticker"] == "CLEAN")
    assert row["intrinsic_at_call"] == 2729.0
    assert row["mos_at_call"] == 0.12
    assert "value_withheld" not in row


def test_the_omission_is_disclosed_not_silent(db):
    out = _withhold_point_estimates(_payload(), db)
    assert "note_value_withheld" in out
    assert "1 call" in out["note_value_withheld"]


def test_guard_failure_never_takes_the_endpoint_down(db):
    db.close()                       # force every query to raise
    out = _withhold_point_estimates(_payload(), db)
    assert len(out["calls"]) == 2    # degrades to the unfiltered payload


def test_THE_ROUTE_actually_applies_it(db, monkeypatch):
    """The tests above exercise the helper directly, so they pass whether or not
    the ROUTE calls it — which is no test at all. This one goes through the
    endpoint, so deleting the call site fails here."""
    import app.backtest_routes as br
    monkeypatch.setattr(br, "compute_backtest", lambda _db: _payload())
    out = br.backtest(db=db)
    row = next(r for r in out["calls"] if r["ticker"] == "SUPPRESSED")
    assert row["intrinsic_at_call"] is None, "the route did not apply the withhold"
    assert out.get("note_value_withheld")
