"""Corporate-action handling: how a stock is shown after a demerger/merger/
rename, and the identity-guard bypass that stays closed unless a successor
symbol is explicitly ISIN-verified."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")

from app import corporate_events as CE


def test_note_only_entry_exposes_banner_but_no_alias():
    ev = CE.for_ticker("TATAMOTORS")
    assert ev is not None
    assert ev["type"] == "demerger" and "demerger" in ev["note"].lower()
    assert set(ev.keys()) == {"type", "effective", "note"}   # internal alias not leaked
    # A new-ISIN demerger must NOT carry a guard-bypass alias.
    assert CE.verified_vendor_alias("TATAMOTORS") is None


def test_unknown_ticker_has_no_event():
    assert CE.for_ticker("TCS") is None
    assert CE.verified_vendor_alias("TCS") is None


def test_case_insensitive_lookup():
    assert CE.for_ticker("tatamotors")["type"] == "demerger"


def test_identity_guard_stays_closed_without_verified_alias(monkeypatch):
    """The whole point: the guard blocks the wrong-security payload for a
    note-only (new-ISIN demerger) ticker, and opens ONLY for an explicitly
    verified alias — never a general match. Ingester imports under 3.13 (CI)."""
    try:
        from app.ingest import indianapi_ingester as ING
    except TypeError:
        import pytest; pytest.skip("ingester uses 3.10+ unions; runs on CI (3.13)")

    # TATAMOTORS: vendor NSE 'TMCV' ≠ ticker, no verified alias → blocked.
    assert ING._identity_ok({"companyProfile": {"exchangeCodeNse": "TMCV"}},
                            "TATAMOTORS") is False
    # Exact match always ok.
    assert ING._identity_ok({"companyProfile": {"exchangeCodeNse": "TATAMOTORS"}},
                            "TATAMOTORS") is True
    # A ticker WITH a verified alias accepts only that alias, nothing else.
    monkeypatch.setattr(CE, "CORPORATE_EVENTS",
                        {"FOO": {"type": "rename", "effective": "2026",
                                 "vendor_alias": "BAR", "note": "x"}})
    assert ING._identity_ok({"companyProfile": {"exchangeCodeNse": "BAR"}}, "FOO") is True
    assert ING._identity_ok({"companyProfile": {"exchangeCodeNse": "BAZ"}}, "FOO") is False
