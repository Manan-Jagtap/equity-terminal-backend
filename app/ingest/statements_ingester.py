"""
statements_ingester.py — multi-year P&L / Balance Sheet / Cash Flow from yfinance.

This is the missing piece that fills the **Financials** tab and the growth /
CAGR ratios. `bulk_ingester` only writes a single current-year FinancialFact;
the multi-year HistoricalFinancial rows were never populated (the XBRL ingester
was a stub). yfinance exposes ~4 fiscal years of statements per ticker, which we
map to the app's canonical line-item names and store in ₹ crore.

Run:
  python -m app.ingest.statements_ingester                 # all companies in DB
  python -m app.ingest.statements_ingester --limit 50      # first 50 (test)
  python -m app.ingest.statements_ingester --ticker TITAN  # one company

Re-run safely — rows are upserted on (company, year, statement, line_item).
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import yfinance as yf
from app.database import SessionLocal, engine, Base
from app import models

Base.metadata.create_all(bind=engine)

CR = 1e7  # ₹ → ₹ crore

# yfinance income-statement row label → canonical line_item
PL_MAP = {
    "Total Revenue": "revenue", "Operating Revenue": "revenue",
    "Cost Of Revenue": "raw_material", "Gross Profit": "gross_profit",
    "EBITDA": "ebitda", "Normalized EBITDA": "ebitda",
    "Reconciled Depreciation": "depreciation",
    "EBIT": "ebit", "Operating Income": "ebit",
    "Interest Expense": "interest_expense",
    "Pretax Income": "pbt", "Tax Provision": "tax",
    "Net Income": "pat", "Net Income Common Stockholders": "pat",
    "Net Interest Income": "nii", "Interest Income": "interest_income",
    "Total Revenue As Reported": "total_income",
}
BS_MAP = {
    "Common Stock Equity": "equity",
    "Stockholders Equity": "net_worth",
    "Total Equity Gross Minority Interest": "total_equity",
    "Retained Earnings": "reserves",
    "Long Term Debt": "lt_debt", "Current Debt": "st_debt",
    "Total Debt": "total_debt",
    "Total Assets": "total_assets",
    "Cash And Cash Equivalents": "cash",
    "Cash Cash Equivalents And Short Term Investments": "cash",
    "Net PPE": "fixed_assets",
    "Investments And Advances": "investments",
    "Total Liabilities Net Minority Interest": "total_liabilities",
}
CF_MAP = {
    "Operating Cash Flow": "operating_cf",
    "Investing Cash Flow": "investing_cf",
    "Financing Cash Flow": "financing_cf",
    "Capital Expenditure": "capex",
    "Free Cash Flow": "fcf",
    "Cash Dividends Paid": "dividends",
    "Changes In Cash": "net_change_cash",
}


def _upsert(db, co, year, stmt, item, value):
    row = (db.query(models.HistoricalFinancial)
             .filter_by(company_id=co.id, fiscal_year=year,
                        statement_type=stmt, line_item=item).first())
    if row:
        row.value = value
    else:
        db.add(models.HistoricalFinancial(
            company_id=co.id, fiscal_year=year, statement_type=stmt,
            line_item=item, value=value, source="yfinance"))


def _ingest_df(db, co, df, mapping, stmt):
    if df is None or getattr(df, "empty", True):
        return 0
    n = 0
    for label, item in mapping.items():
        if label not in df.index:
            continue
        for col in df.columns:
            year = getattr(col, "year", None)
            if year is None:
                continue
            val = df.loc[label, col]
            try:
                v = float(val)
            except (TypeError, ValueError):
                continue
            if v != v:  # NaN
                continue
            _upsert(db, co, int(year), stmt, item, v / CR)
            n += 1
    return n


def _first_nonempty(*dfs):
    """yfinance renamed these accessors across versions; use whichever returns data."""
    for d in dfs:
        if d is not None and not getattr(d, "empty", True):
            return d
    return None


def ingest_statements(db, limit=None, ticker=None):
    q = db.query(models.Company)
    if ticker:
        q = q.filter(models.Company.ticker == ticker.upper())
    companies = q.all()
    if limit:
        companies = companies[:limit]

    print(f"Ingesting multi-year statements for {len(companies)} companies...")
    ok = 0
    for co in companies:
        sym = (co.ticker or "").upper()
        if not sym:
            continue
        try:
            tk = yf.Ticker(sym + ".NS")
            # Modern yfinance: income_stmt / balance_sheet / cash_flow.
            # Legacy fallback: financials / balance_sheet / cashflow.
            inc = _first_nonempty(getattr(tk, "income_stmt", None), getattr(tk, "financials", None))
            bal = _first_nonempty(getattr(tk, "balance_sheet", None))
            cfs = _first_nonempty(getattr(tk, "cash_flow", None), getattr(tk, "cashflow", None))
            n = 0
            n += _ingest_df(db, co, inc, PL_MAP, "PL")
            n += _ingest_df(db, co, bal, BS_MAP, "BS")
            n += _ingest_df(db, co, cfs, CF_MAP, "CF")
            db.commit()
            if n:
                ok += 1
            print(f"  {co.ticker}: {n} statement line-items")
        except Exception as e:
            db.rollback()
            print(f"  {co.ticker}: skipped ({type(e).__name__}: {e})")
        time.sleep(0.3)  # be gentle with Yahoo
    print(f"Done. {ok}/{len(companies)} companies now have multi-year statements.")


if __name__ == "__main__":
    args = sys.argv[1:]
    limit = int(args[args.index("--limit") + 1]) if "--limit" in args else None
    ticker = args[args.index("--ticker") + 1] if "--ticker" in args else None
    db = SessionLocal()
    try:
        ingest_statements(db, limit=limit, ticker=ticker)
    finally:
        db.close()
