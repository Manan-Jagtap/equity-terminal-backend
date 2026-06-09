"""tests/test_operations_logic.py — locks the operating-efficiency extractor.
Run: python tests/test_operations_logic.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.operations_logic import operational_snapshot


def test_extracts_and_trends():
    ratios = {
        "ROCE %": {"Mar 2021": 30.0, "Mar 2022": 32.0, "Mar 2023": 34.0, "Mar 2024": 38.0},
        "ROE %": {"Mar 2023": 25.0, "Mar 2024": 27.0},
        "Debtor Days": {"Mar 2023": 60, "Mar 2024": 55},
        "Inventory Days": {"Mar 2024": 40},
        "Days Payable": {"Mar 2024": 70},
        "Cash Conversion Cycle": {"Mar 2021": 50, "Mar 2022": 45, "Mar 2023": 30, "Mar 2024": 25},
        "Working Capital Days": {"Mar 2024": 35},
    }
    o = operational_snapshot(ratios)
    assert abs(o["roce"] - 38.0) < 1e-6
    assert abs(o["roce_delta_3y"] - 8.0) < 1e-6        # 38 vs 30 (4 points back)
    assert abs(o["roe"] - 27.0) < 1e-6
    assert o["debtor_days"] == 55 and o["inventory_days"] == 40 and o["payable_days"] == 70
    assert o["ccc"] == 25 and abs(o["ccc_delta_3y"] + 25.0) < 1e-6    # 25 vs 50 → -25 (improving)
    print("  ok extract + trends:", o["roce"], o["ccc_delta_3y"])


def test_list_series_supported():
    o = operational_snapshot({"ROCE %": [20, 22, 24, 28]})
    assert abs(o["roce"] - 28) < 1e-6 and abs(o["roce_delta_3y"] - 8) < 1e-6
    print("  ok list series")


def test_unordered_years():
    o = operational_snapshot({"ROCE %": {"FY24": 40, "FY22": 30, "FY23": 35, "FY21": 25}})
    assert abs(o["roce"] - 40) < 1e-6 and abs(o["roce_delta_3y"] - 15) < 1e-6
    print("  ok unordered years")


def test_safe_on_bad_input():
    assert operational_snapshot(None) is None
    assert operational_snapshot({}) is None
    assert operational_snapshot({"Some Unrelated Metric": {"2024": 1}}) is None
    print("  ok safe on bad input")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\nAll {len(fns)} operations-logic tests passed.")
