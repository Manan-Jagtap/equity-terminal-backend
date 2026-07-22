"""Batch 22 — FIX-28 tail (part 2): DATA-08 ingest magnitude sanity. Warn, never
drop — the raw statement still stores; a persistently tiny total-assets/revenue is
surfaced (WARNING) instead of poisoning the statements tab silently (VAML/VISL
stored total_assets = 0.02 cr)."""
from app.ingest.indianapi_ingester import _magnitude_warnings


def test_tiny_total_assets_flagged():
    # a listed company with total_assets = 0.02 cr (₹2 lakh) is implausible
    w = _magnitude_warnings({"revenue": 5000.0, "pat": 400.0, "total_assets": 0.02})
    assert any("total_assets" in x and "small" in x for x in w)


def test_tiny_top_line_flagged():
    # revenue fragment (<10 cr) for a listed name — reuses validate_pl's floor
    w = _magnitude_warnings({"revenue": 1.0, "pat": 0.2})
    assert any("revenue" in x for x in w)


def test_plausible_statement_no_warning():
    w = _magnitude_warnings({"revenue": 12000.0, "total_income": 12500.0,
                             "pat": 1500.0, "pbt": 2000.0, "net_worth": 9000.0,
                             "total_assets": 45000.0})
    assert w == []


def test_missing_keys_never_error():
    # partial payloads must degrade, not raise
    assert _magnitude_warnings({}) == []
    assert isinstance(_magnitude_warnings({"pat": 100.0}), list)
