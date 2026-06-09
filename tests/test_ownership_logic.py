"""tests/test_ownership_logic.py — locks the shareholding snapshot parser.
Run: python tests/test_ownership_logic.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.ownership_logic import ownership_snapshot


def _cat(name, *pairs):
    return {"displayName": name, "categories": [{"holdingDate": d, "percentage": p} for d, p in pairs]}


def _stock(*cats):
    return {"shareholding": list(cats)}


def test_basic_deltas():
    s = _stock(
        _cat("Promoters",            ("2024-12-31", 50.0), ("2025-03-31", 50.0)),
        _cat("Foreign Institutions", ("2024-12-31", 20.0), ("2025-03-31", 22.5)),
        _cat("Domestic Institutions",("2024-12-31", 18.0), ("2025-03-31", 16.0)),
        _cat("Mutual Funds",         ("2024-12-31", 9.0),  ("2025-03-31", 10.2)),
        _cat("Public",               ("2024-12-31", 12.0), ("2025-03-31", 11.5)),
    )
    o = ownership_snapshot(s)
    assert o["as_of"] == "2025-03-31"
    assert abs(o["fii"]["pct"] - 22.5) < 1e-6 and abs(o["fii"]["delta"] - 2.5) < 1e-6
    assert abs(o["dii"]["delta"] + 2.0) < 1e-6           # -2.0
    assert abs(o["mf"]["delta"] - 1.2) < 1e-6
    # institutional = FII + DII; delta = +2.5 - 2.0 = +0.5
    assert abs(o["institutional"]["pct"] - 38.5) < 1e-6
    assert abs(o["institutional"]["delta"] - 0.5) < 1e-6
    print("  ok basic deltas:", o["institutional"])


def test_date_sorting_unordered():
    # entries given out of order → parser sorts chronologically
    s = _stock(_cat("Foreign Institutions", ("2025-03-31", 30.0), ("2024-12-31", 25.0)))
    o = ownership_snapshot(s)
    assert o["as_of"] == "2025-03-31" and abs(o["fii"]["delta"] - 5.0) < 1e-6
    print("  ok unordered dates")


def test_month_year_format():
    s = _stock(_cat("Promoters", ("Dec 2024", 60.0), ("Mar 2025", 58.0)))
    o = ownership_snapshot(s)
    assert o["as_of"] == "Mar 2025" and abs(o["promoter"]["delta"] + 2.0) < 1e-6
    print("  ok MMM YYYY format")


def test_single_quarter_no_delta():
    s = _stock(_cat("Foreign Institutions", ("2025-03-31", 22.0)))
    o = ownership_snapshot(s)
    assert o["fii"]["pct"] == 22.0 and o["fii"]["delta"] is None
    print("  ok single quarter")


def test_safe_on_bad_input():
    assert ownership_snapshot(None) is None
    assert ownership_snapshot({}) is None
    assert ownership_snapshot({"shareholding": []}) is None
    print("  ok safe on bad input")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\nAll {len(fns)} ownership-logic tests passed.")
