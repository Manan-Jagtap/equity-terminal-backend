"""Unit tests for onboarding classification (_resolve_onboarding).

Guards the long-tail bug where a freshly onboarded name with no sector was
silently valued as a generic manufacturer. Pure resolution logic — the DB guard
is only to satisfy the ingester's import-time engine bind."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")

from app.ingest.indianapi_ingester import _resolve_onboarding


def _stock(name, industry):
    return {"companyName": name, "industry": industry, "companyProfile": {"mgIndustry": industry}}


def test_it_services_from_profile():
    name, sector, tmpl, typ, fb = _resolve_onboarding(
        "TCS", _stock("Tata Consultancy Services", "Software & Programming"), "Unknown", "Tcs")
    assert name == "Tata Consultancy Services"
    assert sector == "Software & Programming"
    assert tmpl == "IT_SERVICES" and typ == "nonfinancial" and fb is False


def test_bank_name_is_financial():
    _, _, tmpl, typ, fb = _resolve_onboarding(
        "HDFCBANK", _stock("HDFC Bank Ltd", "Financial Services"), "Unknown", None)
    assert tmpl == "BANK" and typ == "financial" and fb is False


def test_steel_is_manufacturing_template_but_not_a_fallback():
    # steel uses a manufacturing-shaped statement (template) yet a METAL multiple
    # (valuation sector) — so it must NOT be flagged as an unclassified fallback.
    _, _, tmpl, typ, fb = _resolve_onboarding(
        "TATASTEEL", _stock("Tata Steel Ltd", "Iron & Steel"), "Unknown", None)
    assert typ == "nonfinancial" and fb is False


def test_unclassifiable_sector_is_flagged():
    _, _, tmpl, typ, fb = _resolve_onboarding(
        "XYZ", _stock("XYZ Diversified Ltd", "Diversified"), "Unknown", None)
    assert tmpl == "MANUFACTURING" and fb is True


def test_missing_profile_uses_ticker_and_flags_fallback():
    name, sector, tmpl, typ, fb = _resolve_onboarding("NEWCO", {}, "Unknown", None)
    assert name == "NEWCO" and fb is True     # no sector → default multiple, flagged
