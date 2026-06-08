"""
tests/test_forensics.py — locks the forensic report's behaviour and its safety
(no crash on missing/partial data). Run: python tests/test_forensics.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.forensics import forensic_report


def _stmt(year, pat, cfo, ta, rev, gp, ebit, intr, ebitda, borrow, cash, nw, ltd):
    return {year: {
        "PL": {"pat": pat, "revenue": rev, "gross_profit": gp, "ebit": ebit,
               "interest_expense": intr, "ebitda": ebitda},
        "BS": {"total_assets": ta, "borrowings": borrow, "cash": cash,
               "net_worth": nw, "lt_debt": ltd},
        "CF": {"operating_cf": cfo},
    }}


def _healthy(years=(2021, 2022, 2023, 2024)):
    s = {}
    base = dict(pat=100, cfo=110, ta=800, rev=1000, gp=400, ebit=160, intr=10,
                ebitda=200, borrow=120, cash=300, nw=600, ltd=80)
    for i, yr in enumerate(years):
        grow = 1 + 0.10 * i
        s.update(_stmt(yr, base["pat"]*grow, base["cfo"]*grow, base["ta"]*grow,
                       base["rev"]*grow, base["gp"]*grow*1.02, base["ebit"]*grow,
                       base["intr"], base["ebitda"]*grow, base["borrow"],
                       base["cash"]*grow, base["nw"]*grow, base["ltd"]*0.9**i))
    return s


def test_healthy_company_grades_well():
    r = forensic_report(_healthy(), {"type": "nonfinancial"})
    assert r["available"]
    assert r["composite"] is not None and r["composite"] >= 70, r["composite"]
    assert r["grade"] in ("A", "B")
    assert "net_debt_ebitda" in r["metrics"]
    # net cash company → green
    assert r["metrics"]["net_debt_ebitda"]["flag"] == "green"
    print("  ok healthy:", r["grade"], r["composite"])


def test_accrual_heavy_flags_red():
    # PAT far exceeds CFO → high accruals, weak cash conversion.
    s = {}
    for i, yr in enumerate((2021, 2022, 2023, 2024)):
        s.update(_stmt(yr, pat=300, cfo=40, ta=1000, rev=1200, gp=400, ebit=120,
                       intr=60, ebitda=150, borrow=900, cash=20, nw=300, ltd=600))
    r = forensic_report(s, {"type": "nonfinancial"})
    labels = {f["label"] for f in r["flags"]}
    assert "High accruals" in labels or "Weak cash conversion" in labels, labels
    assert r["metrics"]["interest_coverage"]["flag"] in ("amber", "red")
    assert r["composite"] < 60, r["composite"]
    print("  ok accrual-heavy flags:", labels, r["composite"])


def test_missing_data_no_crash():
    assert forensic_report({}, {"type": "nonfinancial"})["available"] is False
    assert forensic_report(None, {})["available"] is False
    # one year only → not enough history
    one = _stmt(2024, 100, 100, 800, 1000, 400, 160, 10, 200, 120, 300, 600, 80)
    assert forensic_report(one, {"type": "nonfinancial"})["available"] is False
    # partial statements: only PL present, no BS/CF
    partial = {2023: {"PL": {"pat": 100, "revenue": 1000}},
               2024: {"PL": {"pat": 110, "revenue": 1100}}}
    r = forensic_report(partial, {"type": "nonfinancial"})
    assert isinstance(r, dict) and "metrics" in r  # no crash
    print("  ok missing-data safe")


def test_financial_reduced_set():
    r = forensic_report(_healthy(), {"type": "financial"})
    assert r["is_financial"] is True
    assert "piotroski" not in r["metrics"]   # not run for lenders
    assert r["note"] and "lender" in r["note"]
    print("  ok financial reduced set")


def test_pending_lists_classic_scores():
    r = forensic_report(_healthy(), {"type": "nonfinancial"})
    joined = " ".join(r["pending"]).lower()
    assert "beneish" in joined and "altman" in joined and "pledge" in joined
    print("  ok pending classics listed")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\nAll {len(fns)} forensics tests passed.")
