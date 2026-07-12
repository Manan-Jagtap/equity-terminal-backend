"""
app/financials.py — Template-aware financial statement formatter.

Takes HistoricalFinancial rows from the DB and shapes them into
the P&L / BS / CF structure the frontend expects, using the
company's template_code to determine which line items to include.

The frontend FinancialStatements.jsx calls:
    GET /api/companies/{ticker}/financials

And expects:
    {
        "template": "NBFC",
        "years_available": [2021, 2022, 2023, 2024, 2025],
        "statements": {
            2025: {
                "PL": { "nii": 14947, "pat": 10590, ... },
                "BS": { "equity": 31245, "borrowings": 120000, ... },
                "CF": { "operating_cf": 12000, "capex": -800, "fcf": 11200 }
            },
            ...
        },
        "growth": {
            "pat_cagr_3y": 0.42,
            "revenue_cagr_3y": 0.35,    # non-fin only
            "nii_cagr_3y": 0.38,        # financial only
        },
        "has_data": true
    }

P&L shapes by template:
  NBFC / BANK / INSURANCE  → NII-based (interest_income, nii, provisions, pat)
  IT_SERVICES / MANUFACTURING / CONSUMER / PHARMA / ENERGY
                           → Revenue-based (revenue, ebitda, ebit, pat)
"""
from __future__ import annotations
from collections import defaultdict
from typing import Any

from .templates import TemplateCode, is_financial


# ── Line items fetched per template ──────────────────────────────────────────
# Maps template_code → list of (statement_type, line_item) pairs to pull.
# Line items are the canonical names stored in historical_financials.line_item.

_PL_FINANCIAL = [
    ("PL", "interest_income"),
    ("PL", "interest_expense"),
    ("PL", "nii"),              # Net Interest Income
    ("PL", "other_income"),
    ("PL", "total_income"),
    ("PL", "opex"),
    ("PL", "provisions"),
    ("PL", "pbt"),
    ("PL", "tax"),
    ("PL", "pat"),
    ("PL", "roe"),              # can be stored as a fact
    ("PL", "roa"),
    ("PL", "nim"),
    ("PL", "cost_to_income"),
]

_PL_NONFINANCIAL = [
    ("PL", "revenue"),
    ("PL", "other_income"),
    ("PL", "total_income"),
    ("PL", "raw_material"),
    ("PL", "gross_profit"),
    ("PL", "ebitda"),
    ("PL", "ebitda_margin"),
    ("PL", "depreciation"),
    ("PL", "ebit"),
    ("PL", "ebit_margin"),
    ("PL", "interest_expense"),
    ("PL", "pbt"),
    ("PL", "tax"),
    ("PL", "pat"),
    ("PL", "pat_margin"),
]

_BS_COMMON = [
    ("BS", "equity"),
    ("BS", "reserves"),
    ("BS", "total_equity"),
    ("BS", "lt_debt"),
    ("BS", "st_debt"),
    ("BS", "borrowings"),       # total borrowings (financial firms use this)
    ("BS", "total_debt"),
    ("BS", "total_liabilities"),
    ("BS", "fixed_assets"),
    ("BS", "investments"),
    ("BS", "cash"),
    ("BS", "total_assets"),
    ("BS", "net_worth"),
    # NBFC/Bank specific
    ("BS", "aum"),
    ("BS", "gnpa"),
    ("BS", "nnpa"),
    ("BS", "crar"),
]

_CF_COMMON = [
    ("CF", "pat"),
    ("CF", "depreciation"),
    ("CF", "operating_cf"),
    ("CF", "investing_cf"),
    ("CF", "financing_cf"),
    ("CF", "capex"),
    ("CF", "fcf"),
    ("CF", "dividends"),
    ("CF", "net_change_cash"),
]


def _items_for_template(template_code: str) -> list[tuple[str, str]]:
    pl = _PL_FINANCIAL if is_financial(template_code) else _PL_NONFINANCIAL
    return pl + _BS_COMMON + _CF_COMMON


# ── Serving-time integrity gate ──────────────────────────────────────────────
# The DB holds statement lines written by different vendors and parsers over
# time (the Reuters-style INC block, /statement supplements, quarterly-results
# parsers). A misfiled quarterly figure or a wrongly-scaled vendor value must
# never render under an annual fiscal-year column. Every rule below is an
# identity that holds for ANY real annual statement, so the gate can only
# remove impossible cells — it never touches a plausible one.

# Cost rows are magnitudes in every published convention (Screener, annual
# reports); the Reuters INC block delivers them negative. P&L only — cash-flow
# outflows keep their sign.
_PL_COST_LINES = ("interest_expense", "opex", "provisions", "depreciation",
                  "raw_material")

# Lines written by the lender P&L supplement — dropped as a block when a
# year fails an identity, because they arrive together from one payload.
_FIN_SUPP_LINES = ("interest_income", "interest_expense", "nii",
                   "other_income", "total_income", "opex", "provisions")


def _sanitize_statements(nested: dict, financial: bool) -> None:
    years = sorted(nested.keys())
    for y in years:
        pl = nested[y].get("PL")
        if not pl:
            continue
        for k in _PL_COST_LINES:
            v = pl.get(k)
            if v is not None and v < 0:
                pl[k] = abs(v)

    for i, y in enumerate(years):
        pl = nested[y].get("PL")
        if not pl:
            continue
        pbt = pl.get("pbt")
        prev = nested[years[i - 1]].get("PL", {}) if i else {}
        bad = False
        if financial:
            # The Reuters INC block files a non-operating scrap value as a
            # lender's interest expense (MUTHOOTFIN: ₹-1.85cr against ₹60,000cr
            # of borrowings). Same gate as metrics cost_of_funds: an implied
            # cost of funds under 1% on a real borrowing book is a misfile.
            ie0 = pl.get("interest_expense")
            bor = nested[y].get("BS", {}).get("borrowings")
            if ie0 is not None and bor and bor > 0 and abs(ie0) < 0.01 * bor:
                pl.pop("interest_expense", None)
            ii, ti, ie = pl.get("interest_income"), pl.get("total_income"), pl.get("interest_expense")
            # Total income funds PBT; a lender's gross top line exceeds PBT.
            if ti is not None and pbt is not None and ti < pbt:
                bad = True
            if ii is not None and pbt is not None and ii < pbt:
                bad = True
            # A going lender's interest bill / top line cannot collapse >80% YoY.
            for cur, pv in ((ie, prev.get("interest_expense")),
                            (ii, prev.get("interest_income"))):
                if cur is not None and pv and cur < 0.2 * abs(pv):
                    bad = True
            if bad:
                for k in _FIN_SUPP_LINES:
                    pl.pop(k, None)
        else:
            # Revenue collapsing >80% YoY while PAT holds is a misfiled period,
            # not a business event.
            rev, prev_rev = pl.get("revenue"), prev.get("revenue")
            pat, prev_pat = pl.get("pat"), prev.get("pat")
            if (rev is not None and prev_rev and rev < 0.2 * prev_rev
                    and pat is not None and prev_pat and abs(pat) > 0.5 * abs(prev_pat)):
                for k in ("revenue", "total_income", "other_income", "raw_material",
                          "gross_profit", "ebitda", "ebitda_margin", "opex"):
                    pl.pop(k, None)


# ── Main formatter ────────────────────────────────────────────────────────────

def build_financials_response(
    company,          # models.Company ORM row
    hist_fins: list,  # list of HistoricalFinancial ORM rows
) -> dict[str, Any]:
    """
    Shape raw DB rows into the nested dict the frontend expects.
    Works regardless of which years/line items are actually populated —
    missing values are omitted (not null-padded), so the frontend's
    existing "—" fallback triggers naturally.
    """
    template = getattr(company, "template_code", None) or TemplateCode.MANUFACTURING
    financial = is_financial(template)

    # Group rows: year → statement_type → line_item → value
    nested: dict[int, dict[str, dict[str, float]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for row in hist_fins:
        if row.value is not None:
            nested[row.fiscal_year][row.statement_type][row.line_item] = row.value

    # Standardise every company to the LATEST 5 fiscal years (some sources
    # returned 7–10). Keep only the 5 most recent so statements are consistent.
    years_available = sorted(nested.keys())[-5:]
    nested = {y: nested[y] for y in years_available}

    _sanitize_statements(nested, financial)

    # Compute derived margins inline where possible
    for yr, stmts in nested.items():
        pl = stmts.get("PL", {})

        if not financial:
            rev = pl.get("revenue")
            if rev and rev > 0:
                ebit = pl.get("ebit")
                ebitda = pl.get("ebitda")
                pat = pl.get("pat")
                if ebit and "ebit_margin" not in pl:
                    pl["ebit_margin"] = ebit / rev
                if ebitda and "ebitda_margin" not in pl:
                    pl["ebitda_margin"] = ebitda / rev
                if pat and "pat_margin" not in pl:
                    pl["pat_margin"] = pat / rev
        else:
            # ROE must use NET WORTH (shareholders' funds), NOT the share-capital
            # line. The BS "equity" item is face-value share capital (~₹500cr for a
            # bank); using it produced absurd ROEs (68–92%). Prefer net_worth, then
            # equity+reserves, and only fall back to share capital if nothing else.
            bs = stmts.get("BS", {})
            eq = (bs.get("net_worth")
                  or ((bs.get("equity") or 0) + (bs.get("reserves") or 0)) or None
                  or bs.get("equity"))
            pat = pl.get("pat")
            if eq and eq > 0 and pat and "roe" not in pl:
                pl["roe"] = pat / eq
            # Cash-flow FCF is meaningless for a lender (no capex-driven FCF) —
            # drop it so the CF statement doesn't imply a misleading number.
            cf = stmts.get("CF")
            if isinstance(cf, dict):
                cf.pop("fcf", None)

    # Growth calculations (need at least 2 years)
    growth: dict[str, float | None] = {}
    if len(years_available) >= 2:
        latest = years_available[-1]
        n = min(len(years_available) - 1, 3)   # up to 3-year CAGR
        oldest = years_available[-(n + 1)]

        def cagr(key: str, stmt: str) -> float | None:
            v0 = nested[oldest][stmt].get(key)
            v1 = nested[latest][stmt].get(key)
            # BOTH ends must be positive: a sign flip makes the fractional
            # power return a COMPLEX number (crashed /financials with a 500
            # for 11 loss-transition names — multi-agent audit finding).
            if v0 and v1 and v0 > 0 and v1 > 0 and n > 0:
                return (v1 / v0) ** (1 / n) - 1
            return None

        growth["pat_cagr_3y"] = cagr("pat", "PL")
        if financial:
            growth["nii_cagr_3y"] = cagr("nii", "PL")
            growth["interest_income_cagr_3y"] = cagr("interest_income", "PL")
        else:
            growth["revenue_cagr_3y"] = cagr("revenue", "PL")
            growth["ebitda_cagr_3y"] = cagr("ebitda", "PL")

    # Convert defaultdicts to plain dicts for JSON serialisation
    statements = {
        yr: {
            stmt: dict(items)
            for stmt, items in stmts.items()
        }
        for yr, stmts in nested.items()
    }

    return {
        "template":        template,
        "is_financial":    financial,
        "years_available": years_available,
        "statements":      statements,
        "growth":          growth,
        "has_data":        len(years_available) > 0,
        "source_note":     (
            "Historical data from yfinance / XBRL ingestion. "
            "Run bulk_ingester to populate."
            if not years_available
            else f"{len(years_available)} fiscal year(s) available."
        ),
    }
