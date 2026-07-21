"""Batch 6 — FIX-16 young/loss-maker + fee-annuity archetypes. Backend-only
(alt_models is NOT mirrored to engine.js) — parity untouched. Both are EARNED
paths: a name missing the required inputs returns None (stays abstained)."""
import os
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_batch6_test.db")
from app import alt_models

_A = {"risk_free": 0.07, "beta": 1.0, "erp": 0.05, "_valuation_sector": "IT_SERVICES"}


def _stmts(years):   # {fy: (revenue, pat)}
    return {y: {"PL": {"revenue": r, "pat": p}} for y, (r, p) in years.items()}


# ── young / loss-maker ────────────────────────────────────────────────────────
def _young_co(**over):
    co = {"type": "nonfinancial", "ticker": "YNG", "sector": "IT Services",
          "revenue": 5000.0, "net_profit": -100.0, "equity": 3000.0, "shares": 100.0,
          "net_debt": 0.0, "series": [], "_valuation_sector": "IT_SERVICES",
          "statements": _stmts({2022: (2000.0, -300.0), 2023: (3000.0, -200.0), 2024: (5000.0, -100.0)})}
    co.update(over)
    return co


def test_fix16_young_eligible_converts():
    v = alt_models.young_company_value(_young_co(), _A)
    assert v and v["intrinsic"] > 0 and v["method"] == "Survival-adjusted (young)"
    assert "survival" in v["note"].lower()


def test_fix16_young_abstains_thin_history():
    co = _young_co(statements=_stmts({2023: (3000.0, -200.0), 2024: (5000.0, -100.0)}))  # 2 yrs
    assert alt_models.young_company_value(co, _A) is None


def test_fix16_young_abstains_when_profitable():
    # ROE ≥ 4% → has a working DCF/RI, not this archetype
    assert alt_models.young_company_value(_young_co(net_profit=600.0), _A) is None


def test_fix16_young_abstains_no_growth_path():
    co = _young_co(statements=_stmts({2022: (5000.0, -100.0), 2023: (4800.0, -120.0), 2024: (4600.0, -140.0)}))
    assert alt_models.young_company_value(co, _A) is None      # shrinking → no path


def test_fix16_young_skips_financials():
    assert alt_models.young_company_value(_young_co(type="financial"), _A) is None


# ── fee-annuity ──────────────────────────────────────────────────────────────
def _fee_co(**over):
    co = {"type": "financial", "ticker": "CDSL", "sector": "Capital Markets",
          "net_profit": 500.0, "shares": 100.0, "series": [], "synthetic_series": True,
          "statements": _stmts({2022: (0.0, 400.0), 2023: (0.0, 450.0), 2024: (0.0, 500.0)})}
    co.update(over)
    return co


def test_fix16_fee_eligible_converts():
    v = alt_models.fee_annuity_value(_fee_co(), _A)
    assert v and v["intrinsic"] > 0 and v["method"] == "Fee earnings-power"
    assert "P/E prior (depository)" in v["components"][1]["label"]   # CDSL → depository sub-type


def test_fix16_fee_abstains_loss_making():
    co = _fee_co(net_profit=-50.0, statements=_stmts({2022: (0.0, -80.0), 2023: (0.0, -60.0), 2024: (0.0, -50.0)}))
    assert alt_models.fee_annuity_value(co, _A) is None           # no positive earnings power


def test_fix16_fee_skips_non_fee():
    assert alt_models.fee_annuity_value(_fee_co(ticker="TCS", sector="IT Services"), _A) is None


def test_fix16_subtype_priors():
    assert alt_models._fee_subtype({"ticker": "BSE"}) == "exchange"
    assert alt_models._fee_subtype({"ticker": "HDFCAMC"}) == "amc"
    assert alt_models._fee_subtype({"ticker": "ZZZ", "sector": "Stock Broking"}) == "broking"
    assert alt_models._fee_subtype({"ticker": "ZZZ", "sector": "Steel"}) == "default"
