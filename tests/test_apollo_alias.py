"""FIX-03 follow-up — the `APOLLO` ticker resolves to Apollo Micro Systems.

Bare "APOLLO" fuzzy-resolved on the vendor to Apollo HOSPITALS (already covered
by the APOLLOHOSP ticker), storing a duplicate under the wrong economics. NSE
symbol APOLLO is Apollo Micro Systems; the alias pins the query so FIX-03's
exchangeCodeNse identity check then accepts it (NSE code APOLLO == ticker)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_apollo.db")

from app.ingest.indianapi_ingester import VENDOR_QUERY_ALIAS


def test_apollo_aliases_to_micro_systems_not_hospitals():
    assert VENDOR_QUERY_ALIAS["APOLLO"] == "Apollo Micro Systems"
    assert "hospital" not in VENDOR_QUERY_ALIAS["APOLLO"].lower()


def test_misresolved_ticker_aliases_are_present():
    # DATA-01 clone-audit fixes — vendor-verified query strings (exchangeCodeNse
    # == ticker) so the FIX-03 identity guard accepts each.
    for tk, want in {
        "ANUP": "Anup Engineering", "CERA": "Cera Sanitaryware",
        "PTC": "PTC India", "TRIVENI": "Triveni Engineering",
    }.items():
        assert VENDOR_QUERY_ALIAS[tk] == want
