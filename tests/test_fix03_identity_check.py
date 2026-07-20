"""FIX-03 / DATA-01 — post-resolution vendor identity check.

`/stock` is a fuzzy NAME search; before this guard the ingester stored whatever
plausible hit came back without checking it was the requested security. VAML and
VISL both resolved to a single micro Vedanta-segment entity and were stored as
byte-identical clones. These tests pin:

  1. the identity predicate (matching/mismatched/absent exchange symbol),
  2. _fetch_stock preferring a matching query form and raising on all-wrong,
  3. the convergence half — a proven-unresolvable name drops out of
     needs_fundamentals so the daily backfill stops looping on it.

Logic-level proof only: confirming the LIVE cohort (VAML/VISL/…) actually carries
a contradicting symbol needs a re-ingest with the real vendor key (owner env)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ["DATABASE_URL"] = "sqlite:////tmp/_pytest_fix03.db"

import pytest

from app.ingest import indianapi_ingester as ing
from app.ingest.indianapi_ingester import (
    _identity_ok, _fetch_stock, VendorIdentityMismatch)
from app.database import Base, engine, SessionLocal
from app import models
from app import coverage


def _stock(name, *, nse=None, bse=None, price=100.0):
    prof = {}
    if nse is not None:
        prof["exchangeCodeNse"] = nse
    if bse is not None:
        prof["exchangeCodeBse"] = bse
    return {"companyName": name, "currentPrice": {"NSE": price},
            "companyProfile": prof}


# ── the identity predicate ───────────────────────────────────────────────────

def test_matching_nse_symbol_is_accepted():
    assert _identity_ok(_stock("Tata Consultancy Services", nse="TCS"), "TCS") is True


def test_mismatched_nse_symbol_is_rejected():
    # the VAML/VISL failure mode: query resolved to a different security whose
    # own NSE code is not our ticker.
    assert _identity_ok(_stock("Vedanta Aluminium Metal", nse="VEDL"), "VAML") is False
    assert _identity_ok(_stock("Vedanta Iron And Steel", nse="VEDL"), "VISL") is False


def test_absent_exchange_symbol_cannot_be_disproven_no_regression():
    # a payload with no companyProfile symbol: we keep prior behaviour (accept),
    # so names the vendor returns without a symbol are never newly broken.
    assert _identity_ok({"companyName": "Some Co", "currentPrice": {}}, "SOMECO") is True
    assert _identity_ok(_stock("Some Co"), "SOMECO") is True


def test_bse_fallback_when_no_nse_code():
    # no NSE code, but a stored BSE scrip code we can check against.
    assert _identity_ok(_stock("Tata Consultancy Services", bse="532540"),
                        "TCS", co_bse="532540") is True
    assert _identity_ok(_stock("Wrong Co", bse="500001"),
                        "TCS", co_bse="532540") is False


def test_symbol_normalisation_ignores_punctuation_and_case():
    assert _identity_ok(_stock("Bajaj Auto", nse="bajaj-auto"), "BAJAJAUTO") is True


# ── _fetch_stock query-form preference ───────────────────────────────────────

def test_fetch_prefers_the_identity_matching_form(monkeypatch):
    # first query form resolves to the WRONG company, second to the right one:
    # _fetch_stock must return the right one, not the first plausible hit.
    calls = {}

    def fake_get(path, params, *a, **k):
        q = params["name"]
        calls[q] = calls.get(q, 0) + 1
        if q == "WRONGCO":                        # bare-ticker alias miss
            return _stock("Wrong Entity", nse="OTHER")
        return _stock("Right Entity", nse="WRONGCO")   # cleaned-name hit is correct

    monkeypatch.setattr(ing, "_get", fake_get)
    stock = _fetch_stock("WRONGCO", co_name="Right Entity Ltd")
    assert stock["companyName"] == "Right Entity"


def test_fetch_raises_when_every_form_is_the_wrong_company(monkeypatch):
    def fake_get(path, params, *a, **k):
        return _stock("Vedanta Aluminium Metal", nse="VEDL")   # always wrong

    monkeypatch.setattr(ing, "_get", fake_get)
    with pytest.raises(VendorIdentityMismatch) as ei:
        _fetch_stock("VAML", co_name="Vardhman Acrylics")
    assert ei.value.ticker == "VAML"
    assert ei.value.vendor_symbol == "VEDL"


def test_fetch_accepts_a_matching_symbol_normally(monkeypatch):
    monkeypatch.setattr(ing, "_get", lambda *a, **k: _stock("Tata Consultancy Services", nse="TCS"))
    assert _fetch_stock("TCS")["companyName"] == "Tata Consultancy Services"


# ── convergence: unresolved names leave the backfill queue ───────────────────

@pytest.fixture()
def db():
    Base.metadata.create_all(engine)
    s = SessionLocal()
    yield s
    s.close()
    Base.metadata.drop_all(engine)


def test_mark_and_clear_vendor_unresolved_round_trip(db):
    assert coverage.vendor_unresolved_set(db) == set()
    coverage.mark_vendor_unresolved(db, "VAML", "VEDL")
    coverage.mark_vendor_unresolved(db, "visl", "VEDL")   # lower-case in → upper stored
    assert coverage.vendor_unresolved_set(db) == {"VAML", "VISL"}
    # idempotent
    coverage.mark_vendor_unresolved(db, "VAML", "VEDL")
    assert coverage.vendor_unresolved_set(db) == {"VAML", "VISL"}
    assert coverage.clear_vendor_unresolved(db, "VAML") is True
    assert coverage.vendor_unresolved_set(db) == {"VISL"}
    assert coverage.clear_vendor_unresolved(db, "NOPE") is False


def test_needs_fundamentals_excludes_unresolved(db, monkeypatch):
    rows = [
        {"ticker": "AAA", "statements": 0, "has_insight": False},   # needs backfill
        {"ticker": "VAML", "statements": 0, "has_insight": False},  # unresolvable
        {"ticker": "BBB", "statements": 7, "has_insight": True},    # complete
    ]
    monkeypatch.setattr(coverage, "coverage_rows", lambda db, tickers: rows)
    coverage.mark_vendor_unresolved(db, "VAML", "VEDL")
    got = coverage.needs_fundamentals(db, ["AAA", "VAML", "BBB"])
    assert "AAA" in got            # a genuinely-thin name still gets retried
    assert "VAML" not in got       # the unresolvable name drops out → loop converges
    assert "BBB" not in got        # complete name never selected
