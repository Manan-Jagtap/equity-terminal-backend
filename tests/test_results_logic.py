"""tests/test_results_logic.py — locks the EPS-surprise parser.
Run: python tests/test_results_logic.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.results_logic import eps_surprise


def _fc(periods):
    return {"eps": {"measureCode": "EPS", "periods": periods}}


def _period(num, year, reported, mean, pct=None, date=None, actual_as_dict=False):
    actual = {"Reported": reported, "SurpriseMean": mean, "SurprisePercent": pct,
              "ReportedDate": date}
    return {"RelativePeriod": {"Number": num}, "FiscalPeriod": {"Year": year},
            "ActualReportDate": date,
            "Actuals": {"Actual": actual if actual_as_dict else [actual]}}


def test_latest_reported_wins():
    fc = _fc([
        _period(-2, 2024, 125.88, 126.38, -0.4, "2024-04-12T06:13:00"),
        _period(-1, 2025, 134.20, 132.00, 1.67, "2025-04-10T06:00:00"),
    ])
    s = eps_surprise(fc)
    assert s["fy"] == 2025 and abs(s["reported"] - 134.20) < 1e-6
    assert s["beat"] is True and abs(s["surprise_pct"] - 1.67) < 1e-6
    print("  ok latest reported wins:", s["fy"], s["surprise_pct"])


def test_miss_flagged():
    s = eps_surprise(_fc([_period(-1, 2025, 90.0, 100.0, -10.0, "2025-04-01")]))
    assert s["beat"] is False and s["surprise_pct"] == -10.0
    print("  ok miss flagged")


def test_derive_pct_when_missing():
    s = eps_surprise(_fc([_period(-1, 2025, 110.0, 100.0, None, "2025-04-01")]))
    assert abs(s["surprise_pct"] - 10.0) < 1e-6 and s["beat"] is True
    print("  ok derived pct")


def test_actual_as_dict_shape():
    s = eps_surprise(_fc([_period(-1, 2025, 50.0, 49.0, 2.04, "2025-04-01", actual_as_dict=True)]))
    assert s and abs(s["reported"] - 50.0) < 1e-6
    print("  ok dict-shaped actual")


def test_no_actuals_or_bad_input():
    assert eps_surprise(None) is None
    assert eps_surprise({}) is None
    assert eps_surprise({"eps": {"periods": [{"RelativePeriod": {"Number": 1}}]}}) is None  # forward only
    print("  ok no-data safe")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\nAll {len(fns)} results-logic tests passed.")
