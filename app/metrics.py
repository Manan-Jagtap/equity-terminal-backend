"""
app/metrics.py — Declarative metric registry.

Computes 80+ ratios from FinancialFact rows + HistoricalFinancial rows
+ MarketSnapshot. Each metric is tagged with the templates it applies to
so the frontend Ratios tab knows what to show per sector.

Used by:
    GET /api/companies/{ticker}/metrics
    GET /api/companies/{ticker}/metrics?category=Profitability

Response shape:
    {
        "ticker": "MUTHOOTFIN",
        "template": "NBFC",
        "categories": [
            {
                "name": "Growth",
                "metrics": [
                    {"key": "pat_growth_yoy", "label": "PAT Growth (YoY)",
                     "value": 0.98, "formatted": "98.0%", "unit": "pct",
                     "good": true, "note": null},
                    ...
                ]
            },
            ...
        ]
    }
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable
from collections import defaultdict

from .templates import TemplateCode, is_financial, ALL_TEMPLATES

# ── Sentinel for "not computable from available data" ──────────────────────
_NA = None


# ── Metric descriptor ──────────────────────────────────────────────────────
@dataclass
class Metric:
    key:       str
    label:     str
    category:  str
    unit:      str          # "pct" | "x" | "inr" | "cr" | "number" | "bps"
    templates: tuple        # which template_codes this metric applies to
    fn:        Callable     # fn(ctx) -> float | None
    good_dir:  str = "high" # "high" = higher is better, "low" = lower is better, "none"
    fmt_scale: float = 1.0  # multiply before formatting (e.g. 100 for pct display)
    note:      str | None = None

    def compute(self, ctx: dict) -> float | None:
        try:
            return self.fn(ctx)
        except Exception:
            return _NA

    def format(self, value: float | None) -> str:
        if value is None:
            return "—"
        v = value * self.fmt_scale
        if self.unit == "pct":
            return f"{v:.1f}%"
        if self.unit == "x":
            return f"{v:.2f}x"
        if self.unit == "inr":
            return f"₹{v:,.1f}"
        if self.unit == "cr":
            return f"₹{v:,.0f} cr"
        if self.unit == "bps":
            return f"{v*100:.0f} bps"
        return f"{v:,.2f}"

    def is_good(self, value: float | None) -> bool | None:
        if value is None:
            return None
        if self.good_dir == "high":
            return value > 0
        if self.good_dir == "low":
            return value < 0.05
        return None


ALL_TEMPLATES_TUPLE = tuple(ALL_TEMPLATES)
NON_FIN = (
    TemplateCode.IT_SERVICES, TemplateCode.MANUFACTURING,
    TemplateCode.CONSUMER, TemplateCode.PHARMA, TemplateCode.ENERGY,
)
FINANCIAL = (TemplateCode.NBFC, TemplateCode.BANK, TemplateCode.INSURANCE)


# ── Safe arithmetic helpers ───────────────────────────────────────────────
def _div(a, b):
    if a is None or b is None or b == 0:
        return None
    return a / b

def _pct_change(new, old):
    if new is None or old is None or old == 0:
        return None
    return (new - old) / abs(old)

def _cagr(v0, v1, n):
    if v0 is None or v1 is None or v0 <= 0 or n <= 0:
        return None
    return (v1 / v0) ** (1 / n) - 1

def _get(ctx, *keys, default=None):
    """Traverse nested ctx dict safely."""
    cur = ctx
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


# ── Context builder ───────────────────────────────────────────────────────
def build_context(company, facts: dict, hist: dict, price: float,
                  market_cap: float | None) -> dict:
    """
    Flatten all data sources into a single dict the metric functions read.

    hist shape (from build_financials_response):
        { year: { "PL": {...}, "BS": {...}, "CF": {...} } }
    """
    years = sorted(hist.keys())
    latest_yr = years[-1] if years else None
    prev_yr   = years[-2] if len(years) >= 2 else None
    # Only expose a "3 years ago" datapoint when the history really spans 3+
    # years; the _cagr metrics divide by a fixed n=3, so feeding them a 1-2 year
    # old value silently understated/overstated every "3Y CAGR".
    yr_3ago   = years[-4] if len(years) >= 4 else None

    def pl(yr, k):  return _get(hist, yr, "PL", k) if yr else None
    def bs(yr, k):  return _get(hist, yr, "BS", k) if yr else None
    def cf(yr, k):  return _get(hist, yr, "CF", k) if yr else None

    shares = company.shares_outstanding
    # Prefer NET WORTH (shareholders' funds). The BS "equity" line is face-value
    # share capital (~₹500cr for a large bank vs ~₹2.5L cr net worth); using it
    # first inflated ROE/BVPS-derived metrics ~20-50x. Mirrors financials.py.
    equity = (facts.get("NET_WORTH")
              or bs(latest_yr, "net_worth")
              or (((bs(latest_yr, "equity") or 0) + (bs(latest_yr, "reserves") or 0)) or None)
              or bs(latest_yr, "equity"))

    # Latest-year line items
    pat_l   = pl(latest_yr, "pat")   or facts.get("NET_PROFIT")
    pat_p   = pl(prev_yr,   "pat")
    pat_3   = pl(yr_3ago,   "pat")

    rev_l   = pl(latest_yr, "revenue")  or facts.get("REVENUE")
    rev_p   = pl(prev_yr,   "revenue")
    rev_3   = pl(yr_3ago,   "revenue")

    nii_l   = pl(latest_yr, "nii")
    nii_p   = pl(prev_yr,   "nii")
    nii_3   = pl(yr_3ago,   "nii")

    ebit_l  = pl(latest_yr, "ebit")
    ebitda_l= pl(latest_yr, "ebitda")
    pbt_l   = pl(latest_yr, "pbt")
    int_l   = pl(latest_yr, "interest") or pl(latest_yr, "interest_expense")

    total_assets = bs(latest_yr, "total_assets")
    borrowings   = bs(latest_yr, "borrowings") or bs(latest_yr, "total_debt")
    cash         = bs(latest_yr, "cash")
    lt_debt      = bs(latest_yr, "lt_debt")

    op_cf   = cf(latest_yr, "operating_cf")
    capex   = cf(latest_yr, "capex")
    fcf     = cf(latest_yr, "fcf")
    div_paid= cf(latest_yr, "dividends")

    net_debt = facts.get("NET_DEBT")
    if net_debt is None and borrowings is not None and cash is not None:
        net_debt = borrowings - cash

    # NBFC-specific
    aum  = facts.get("AUM")
    gnpa = facts.get("GNPA")
    nnpa = facts.get("NNPA")
    crar = facts.get("CRAR")
    nim  = facts.get("NIM")
    roa  = facts.get("ROA")

    return {
        "price": price, "shares": shares, "market_cap": market_cap,
        "equity": equity,
        "pat_l": pat_l, "pat_p": pat_p, "pat_3": pat_3,
        "rev_l": rev_l, "rev_p": rev_p, "rev_3": rev_3,
        "nii_l": nii_l, "nii_p": nii_p, "nii_3": nii_3,
        "ebit_l": ebit_l, "ebitda_l": ebitda_l,
        "pbt_l": pbt_l, "int_l": int_l,
        "total_assets": total_assets,
        "borrowings": borrowings, "cash": cash, "lt_debt": lt_debt,
        "net_debt": net_debt,
        "op_cf": op_cf, "capex": capex, "fcf": fcf, "div_paid": div_paid,
        "aum": aum, "gnpa": gnpa, "nnpa": nnpa, "crar": crar,
        "nim": nim, "roa": roa,
        "n_years": len(years),
    }


# ── Metric registry ───────────────────────────────────────────────────────
METRICS: list[Metric] = [

    # ── GROWTH ──────────────────────────────────────────────────────────────
    Metric("pat_growth_yoy",  "PAT Growth (YoY)",     "Growth", "pct", ALL_TEMPLATES_TUPLE,
           lambda c: _pct_change(c["pat_l"], c["pat_p"]), fmt_scale=100),

    Metric("pat_cagr_3y",     "PAT CAGR (3Y)",        "Growth", "pct", ALL_TEMPLATES_TUPLE,
           lambda c: _cagr(c["pat_3"], c["pat_l"], 3), fmt_scale=100),

    Metric("rev_growth_yoy",  "Revenue Growth (YoY)", "Growth", "pct", NON_FIN,
           lambda c: _pct_change(c["rev_l"], c["rev_p"]), fmt_scale=100),

    Metric("rev_cagr_3y",     "Revenue CAGR (3Y)",    "Growth", "pct", NON_FIN,
           lambda c: _cagr(c["rev_3"], c["rev_l"], 3), fmt_scale=100),

    Metric("nii_growth_yoy",  "NII Growth (YoY)",     "Growth", "pct", FINANCIAL,
           lambda c: _pct_change(c["nii_l"], c["nii_p"]), fmt_scale=100),

    Metric("nii_cagr_3y",     "NII CAGR (3Y)",        "Growth", "pct", FINANCIAL,
           lambda c: _cagr(c["nii_3"], c["nii_l"], 3), fmt_scale=100),

    # ── PROFITABILITY ────────────────────────────────────────────────────────
    Metric("roe",             "Return on Equity",     "Profitability", "pct", ALL_TEMPLATES_TUPLE,
           lambda c: _div(c["pat_l"], c["equity"]), fmt_scale=100),

    Metric("roa",             "Return on Assets",     "Profitability", "pct", ALL_TEMPLATES_TUPLE,
           lambda c: c["roa"] or _div(c["pat_l"], c["total_assets"]), fmt_scale=100),

    Metric("pat_margin",      "PAT Margin",           "Profitability", "pct", NON_FIN,
           lambda c: _div(c["pat_l"], c["rev_l"]), fmt_scale=100),

    Metric("ebit_margin",     "EBIT Margin",          "Profitability", "pct", NON_FIN,
           lambda c: _div(c["ebit_l"], c["rev_l"]), fmt_scale=100),

    Metric("ebitda_margin",   "EBITDA Margin",        "Profitability", "pct", NON_FIN,
           lambda c: _div(c["ebitda_l"], c["rev_l"]), fmt_scale=100),

    Metric("nim_metric",      "Net Interest Margin",  "Profitability", "pct", FINANCIAL,
           lambda c: c["nim"], fmt_scale=100),

    Metric("spread",          "Interest Spread",      "Profitability", "pct", FINANCIAL,
           lambda c: (c["nim"] * 1.2) if c["nim"] else None, fmt_scale=100,
           note="Approx = NIM × 1.2 when spread not directly available"),

    # ── RETURNS ─────────────────────────────────────────────────────────────
    Metric("roce",            "Return on Capital Employed", "Returns", "pct", NON_FIN,
           lambda c: _div(c["ebit_l"],
               (c["equity"] or 0) + (c["borrowings"] or 0) - (c["cash"] or 0)),
           fmt_scale=100),

    Metric("roic",            "Return on Invested Capital", "Returns", "pct", NON_FIN,
           lambda c: _div(c["pat_l"],
               (c["equity"] or 0) + (c["lt_debt"] or 0)),
           fmt_scale=100),

    # ── LIQUIDITY ────────────────────────────────────────────────────────────
    Metric("cash_cr",         "Cash & Equivalents",   "Liquidity", "cr", ALL_TEMPLATES_TUPLE,
           lambda c: c["cash"], good_dir="high"),

    Metric("interest_cover",  "Interest Coverage",    "Liquidity", "x", NON_FIN,
           lambda c: _div(c["ebit_l"], c["int_l"]),
           good_dir="high", note="EBIT / Interest Expense"),

    Metric("op_cf_cover",     "Operating CF / PAT",   "Liquidity", "x", ALL_TEMPLATES_TUPLE,
           lambda c: _div(c["op_cf"], c["pat_l"]),
           note="Cash conversion quality; >1 preferred"),

    # ── LEVERAGE ─────────────────────────────────────────────────────────────
    Metric("debt_equity",     "Debt / Equity",        "Leverage", "x", NON_FIN,
           lambda c: _div(c["borrowings"], c["equity"]),
           good_dir="low"),

    Metric("net_debt_equity", "Net Debt / Equity",    "Leverage", "x", NON_FIN,
           lambda c: _div(c["net_debt"], c["equity"]),
           good_dir="low"),

    Metric("net_debt_ebitda", "Net Debt / EBITDA",    "Leverage", "x", NON_FIN,
           lambda c: _div(c["net_debt"], c["ebitda_l"]),
           good_dir="low", note="<2x conservative; >4x stretched"),

    Metric("leverage_ratio",  "Leverage Ratio",       "Leverage", "x", FINANCIAL,
           lambda c: _div(c["total_assets"], c["equity"]),
           good_dir="none", note="Total Assets / Equity; typical NBFC 4-8x"),

    Metric("crar_metric",     "CRAR",                 "Leverage", "pct", FINANCIAL,
           lambda c: c["crar"], fmt_scale=100, good_dir="high",
           note="Capital adequacy; RBI minimum 15% for NBFCs"),

    # ── EFFICIENCY ───────────────────────────────────────────────────────────
    Metric("asset_turnover",  "Asset Turnover",       "Efficiency", "x", NON_FIN,
           lambda c: _div(c["rev_l"], c["total_assets"]),
           note="Revenue / Total Assets"),

    Metric("rev_per_employee","Revenue / Employee",   "Efficiency", "cr", (TemplateCode.IT_SERVICES,),
           lambda c: None,   # placeholder — needs headcount data
           note="Requires headcount ingestion"),

    Metric("aum_per_cr_equity","AUM / Equity",        "Efficiency", "x", FINANCIAL,
           lambda c: _div(c["aum"], c["equity"]),
           note="Capital deployment efficiency for NBFCs"),

    # ── CASH FLOW ────────────────────────────────────────────────────────────
    Metric("fcf_cr",          "Free Cash Flow",       "Cash Flow", "cr", ALL_TEMPLATES_TUPLE,
           lambda c: c["fcf"] or (
               (c["op_cf"] or 0) + (c["capex"] or 0)
               if c["op_cf"] is not None else None
           ), good_dir="high"),

    Metric("fcf_yield",       "FCF Yield",            "Cash Flow", "pct", NON_FIN,
           lambda c: _div(
               c["fcf"] or ((c["op_cf"] or 0) + (c["capex"] or 0) if c["op_cf"] else None),
               c["market_cap"]
           ), fmt_scale=100, good_dir="high"),

    Metric("capex_intensity",  "CapEx / Revenue",     "Cash Flow", "pct", NON_FIN,
           lambda c: abs(_div(c["capex"], c["rev_l"])) if c["capex"] else None,
           fmt_scale=100, good_dir="low"),

    Metric("div_payout",      "Dividend Payout",      "Cash Flow", "pct", ALL_TEMPLATES_TUPLE,
           lambda c: abs(_div(c["div_paid"], c["pat_l"])) if c["div_paid"] else None,
           fmt_scale=100, good_dir="none"),

    # ── PER SHARE ────────────────────────────────────────────────────────────
    Metric("eps",             "Earnings Per Share",   "Per Share", "inr", ALL_TEMPLATES_TUPLE,
           lambda c: _div(c["pat_l"], c["shares"]),
           good_dir="high"),

    Metric("bvps",            "Book Value Per Share", "Per Share", "inr", ALL_TEMPLATES_TUPLE,
           lambda c: _div(c["equity"], c["shares"]),
           good_dir="high"),

    Metric("dps",             "Dividend Per Share",   "Per Share", "inr", ALL_TEMPLATES_TUPLE,
           lambda c: abs(_div(c["div_paid"], c["shares"])) if c["div_paid"] else None,
           good_dir="none"),

    Metric("cfps",            "Cash Flow Per Share",  "Per Share", "inr", ALL_TEMPLATES_TUPLE,
           lambda c: _div(c["op_cf"], c["shares"]),
           good_dir="high"),

    # ── VALUATION ────────────────────────────────────────────────────────────
    Metric("pe_ratio",        "P/E Ratio",            "Valuation", "x", ALL_TEMPLATES_TUPLE,
           lambda c: _div(c["price"], _div(c["pat_l"], c["shares"])),
           good_dir="low", note="Lower vs peers is attractive"),

    Metric("pb_ratio",        "P/B Ratio",            "Valuation", "x", ALL_TEMPLATES_TUPLE,
           lambda c: _div(c["price"], _div(c["equity"], c["shares"])),
           good_dir="none"),

    Metric("ps_ratio",        "P/S Ratio",            "Valuation", "x", NON_FIN,
           lambda c: _div(c["market_cap"], c["rev_l"]),
           good_dir="low"),

    Metric("ev_ebitda",       "EV / EBITDA",          "Valuation", "x", NON_FIN,
           lambda c: _div(
               (c["market_cap"] or 0) + (c["net_debt"] or 0),
               c["ebitda_l"]
           ), good_dir="low"),

    Metric("ev_ebit",         "EV / EBIT",            "Valuation", "x", NON_FIN,
           lambda c: _div(
               (c["market_cap"] or 0) + (c["net_debt"] or 0),
               c["ebit_l"]
           ), good_dir="low"),

    Metric("p_aum",           "Price / AUM",          "Valuation", "pct", FINANCIAL,
           lambda c: _div(c["market_cap"], c["aum"]),
           fmt_scale=100, good_dir="low",
           note="Market cap as % of AUM; key NBFC valuation metric"),

    Metric("earnings_yield",  "Earnings Yield",       "Valuation", "pct", ALL_TEMPLATES_TUPLE,
           lambda c: _div(_div(c["pat_l"], c["shares"]), c["price"]),
           fmt_scale=100, good_dir="high"),

    # ── CAPITAL STRUCTURE ────────────────────────────────────────────────────
    Metric("total_assets_cr", "Total Assets",         "Capital Structure", "cr", ALL_TEMPLATES_TUPLE,
           lambda c: c["total_assets"], good_dir="none"),

    Metric("equity_cr",       "Net Worth",            "Capital Structure", "cr", ALL_TEMPLATES_TUPLE,
           lambda c: c["equity"], good_dir="none"),

    Metric("borrowings_cr",   "Total Borrowings",     "Capital Structure", "cr", ALL_TEMPLATES_TUPLE,
           lambda c: c["borrowings"], good_dir="none"),

    Metric("net_debt_cr",     "Net Debt",             "Capital Structure", "cr", ALL_TEMPLATES_TUPLE,
           lambda c: c["net_debt"], good_dir="none"),

    Metric("market_cap_cr",   "Market Cap",           "Capital Structure", "cr", ALL_TEMPLATES_TUPLE,
           lambda c: c["market_cap"], good_dir="none"),

    # ── BANKING / NBFC SPECIFIC ──────────────────────────────────────────────
    Metric("aum_cr",          "AUM",                  "NBFC / Banking", "cr", FINANCIAL,
           lambda c: c["aum"], good_dir="high"),

    Metric("gnpa_pct",        "GNPA Ratio",           "NBFC / Banking", "pct", FINANCIAL,
           lambda c: c["gnpa"], fmt_scale=100, good_dir="low",
           note="<2% excellent; 2-4% acceptable; >4% elevated"),

    Metric("nnpa_pct",        "NNPA Ratio",           "NBFC / Banking", "pct", FINANCIAL,
           lambda c: c["nnpa"], fmt_scale=100, good_dir="low",
           note="Should be <1% for well-provisioned NBFC"),

    Metric("pcr",             "Provision Coverage",   "NBFC / Banking", "pct", FINANCIAL,
           lambda c: (1 - _div(c["nnpa"], c["gnpa"])) if c["gnpa"] and c["nnpa"] else None,
           fmt_scale=100, good_dir="high",
           note="(1 − NNPA/GNPA) × 100; >70% adequate"),

    Metric("credit_cost",     "Implied Credit Cost",  "NBFC / Banking", "pct", FINANCIAL,
           lambda c: _div(
               _get(c, "provisions") or (_div(c["pat_l"], 0.9) - c["pat_l"]),
               c["aum"]
           ), fmt_scale=100, good_dir="low"),

    Metric("cost_of_funds",   "Cost of Funds (approx)", "NBFC / Banking", "pct", FINANCIAL,
           lambda c: _div(c.get("int_l"), c["borrowings"]) if c.get("int_l") else None,
           fmt_scale=100, good_dir="low",
           note="Interest expense / avg borrowings"),

    Metric("nim_spread",      "NIM − CoF Spread",     "NBFC / Banking", "pct", FINANCIAL,
           lambda c: (
               (c["nim"] or 0) - (_div(c.get("int_l"), c["borrowings"]) or 0)
           ) if c["nim"] else None,
           fmt_scale=100, good_dir="high"),

    Metric("aum_growth_yoy",  "AUM Growth (YoY)",     "NBFC / Banking", "pct", FINANCIAL,
           lambda c: None,   # needs prior-year AUM — requires multi-year NBFC fact storage
           fmt_scale=100, good_dir="high",
           note="Requires prior-year AUM in financial_facts"),

    # ── INSURANCE SPECIFIC ───────────────────────────────────────────────────
    Metric("combined_ratio",  "Combined Ratio",       "Insurance", "pct",
           (TemplateCode.INSURANCE,),
           lambda c: None,   # needs claims + opex / premiums
           fmt_scale=100, good_dir="low",
           note="Requires premium/claims data"),

    # ── PHARMA SPECIFIC ──────────────────────────────────────────────────────
    Metric("rd_intensity",    "R&D / Revenue",        "Pharma", "pct",
           (TemplateCode.PHARMA,),
           lambda c: None,   # needs R&D spend line item
           fmt_scale=100, good_dir="none",
           note="Requires R&D expense ingestion"),

    # ── ENERGY SPECIFIC ──────────────────────────────────────────────────────
    Metric("ev_ebitda_energy","EV / EBITDA (Energy)",  "Energy Metrics", "x",
           (TemplateCode.ENERGY,),
           lambda c: _div(
               (c["market_cap"] or 0) + (c["net_debt"] or 0),
               c["ebitda_l"]
           ), good_dir="low"),

    # ── MARKET ───────────────────────────────────────────────────────────────
    Metric("mcap_cr",         "Market Cap (₹ cr)",    "Market", "cr", ALL_TEMPLATES_TUPLE,
           lambda c: c["market_cap"], good_dir="none"),

    Metric("price_52w_vs",    "52W Price",            "Market", "inr", ALL_TEMPLATES_TUPLE,
           lambda c: c["price"], good_dir="none"),
]

# Registry index for fast lookup
METRIC_BY_KEY: dict[str, Metric] = {m.key: m for m in METRICS}


# ── Response builder ──────────────────────────────────────────────────────
def compute_metrics(
    company,
    facts: dict,
    hist_statements: dict,
    price: float,
    template_code: str,
    category_filter: str | None = None,
) -> dict:
    """
    Compute all applicable metrics for this company and return the
    categorised response dict.

    hist_statements: the 'statements' dict from build_financials_response,
        keyed by fiscal_year int.
    """
    market_cap = price * company.shares_outstanding if price else None

    ctx = build_context(company, facts, hist_statements, price, market_cap)

    # Filter metrics by template applicability
    applicable = [m for m in METRICS if template_code in m.templates]
    if category_filter:
        applicable = [m for m in applicable if m.category.lower() == category_filter.lower()]

    # Group by category preserving order
    cat_order: list[str] = []
    cat_metrics: dict[str, list] = defaultdict(list)
    for m in applicable:
        if m.category not in cat_order:
            cat_order.append(m.category)
        value = m.compute(ctx)
        cat_metrics[m.category].append({
            "key":       m.key,
            "label":     m.label,
            "value":     value,
            "formatted": m.format(value),
            "unit":      m.unit,
            "good":      m.is_good(value),
            "note":      m.note,
        })

    # Only include categories that have at least one non-None value
    categories = []
    for cat in cat_order:
        items = cat_metrics[cat]
        has_values = any(i["value"] is not None for i in items)
        if has_values or not category_filter:
            categories.append({"name": cat, "metrics": items})

    return {
        "ticker":    company.ticker,
        "template":  template_code,
        "price":     price,
        "market_cap": market_cap,
        "categories": categories,
        "total_metrics": sum(len(c["metrics"]) for c in categories),
        "populated_metrics": sum(
            1 for c in categories for m in c["metrics"] if m["value"] is not None
        ),
    }
