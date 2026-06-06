"""
statements_ingester.py — multi-year P&L / Balance Sheet / Cash Flow from yfinance.

Fills the **Financials** tab and the growth / CAGR ratios. bulk_ingester only
wrote a single current-year FinancialFact; the multi-year HistoricalFinancial
rows were never populated. yfinance exposes ~4 fiscal years of statements per
ticker, mapped here to the app's canonical line-item names and stored in ₹ crore.

Robust by design:
  * de-duplicates within a company (several yfinance labels can map to the same
    canonical field — last value wins, no duplicate-key crash);
  * a fresh DB session per company with one retry, so a dropped connection on
    one company never kills the whole run;
  * yfinance fetched OUTSIDE the DB session to keep transactions short.

Run:
  python -m app.ingest.statements_ingester                 # all companies
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

PL_MAP = {
    "Total Revenue": "revenue", "Operating Revenue": "revenue",
    "Total Revenue As Reported": "total_income",
    "Cost Of Revenue": "raw_material", "Gross Profit": "gross_profit",
    "Operating Expense": "opex",
    "Total Expenses": "total_expenses",
    "Other Income Expense": "other_income", "Total Other Finance Cost": "other_income",
    "EBITDA": "ebitda", "Normalized EBITDA": "ebitda",
    "Reconciled Depreciation": "depreciation",
    "Depreciation And Amortization In Income Statement": "depreciation",
    "EBIT": "ebit", "Operating Income": "ebit",
    "Interest Expense": "interest_expense", "Net Non Operating Interest Income Expense": "interest_expense",
    "Pretax Income": "pbt", "Tax Provision": "tax",
    "Net Income": "pat", "Net Income Common Stockholders": "pat",
    "Net Interest Income": "nii", "Interest Income": "interest_income",
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


def _first_nonempty(*dfs):
    """yfinance renamed these accessors across versions; use whichever returns data."""
    for d in dfs:
        if d is not None and not getattr(d, "empty", True):
            return d
    return None


def _collect(df, mapping, stmt, staged):
    """Stage values into staged[(year, stmt, item)] = value. Last write wins,
    which dedupes the case where two yfinance labels map to the same field."""
    if df is None or getattr(df, "empty", True):
        return
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
            staged[(int(year), stmt, item)] = v / CR


def _write(cid, staged):
    """Upsert one company's staged rows in a fresh session; retry once on a
    transient network error. Returns True on success."""
    for attempt in (1, 2):
        s = SessionLocal()
        try:
            for (year, stmt, item), v in staged.items():
                row = (s.query(models.HistoricalFinancial)
                         .filter_by(company_id=cid, fiscal_year=year,
                                    statement_type=stmt, line_item=item).first())
                if row:
                    row.value = v
                else:
                    s.add(models.HistoricalFinancial(
                        company_id=cid, fiscal_year=year, statement_type=stmt,
                        line_item=item, value=v, source="yfinance"))
            s.commit()
            return True
        except Exception as e:
            s.rollback()
            if attempt == 1 and "network" in str(e).lower():
                time.sleep(3)
                continue
            raise
        finally:
            s.close()
    return False


def ingest_statements(db=None, limit=None, ticker=None):
    # Read the company list with a throwaway session, then release it — we don't
    # hold one long-lived connection across the whole (slow) run.
    s0 = SessionLocal()
    q = s0.query(models.Company)
    if ticker:
        q = q.filter(models.Company.ticker == ticker.upper())
    targets = [(c.id, c.ticker) for c in q.all()]
    s0.close()
    if limit:
        targets = targets[:limit]

    print(f"Ingesting multi-year statements for {len(targets)} companies...")
    ok = 0
    for cid, tkr in targets:
        sym = (tkr or "").upper()
        if not sym:
            continue
        try:
            tk = yf.Ticker(sym + ".NS")
            inc = _first_nonempty(getattr(tk, "income_stmt", None), getattr(tk, "financials", None))
            bal = _first_nonempty(getattr(tk, "balance_sheet", None))
            cfs = _first_nonempty(getattr(tk, "cash_flow", None), getattr(tk, "cashflow", None))
        except Exception as e:
            print(f"  {tkr}: yfinance fetch failed ({type(e).__name__})")
            continue

        staged = {}
        _collect(inc, PL_MAP, "PL", staged)
        _collect(bal, BS_MAP, "BS", staged)
        _collect(cfs, CF_MAP, "CF", staged)
        if not staged:
            print(f"  {tkr}: no statement data on Yahoo")
            continue

        try:
            _write(cid, staged)
            ok += 1
            print(f"  {tkr}: {len(staged)} statement line-items")
        except Exception as e:
            print(f"  {tkr}: db write failed ({type(e).__name__})")
        time.sleep(0.3)  # be gentle with Yahoo

    print(f"Done. {ok}/{len(targets)} companies now have multi-year statements.")


if __name__ == "__main__":
    args = sys.argv[1:]
    limit = int(args[args.index("--limit") + 1]) if "--limit" in args else None
    ticker = args[args.index("--ticker") + 1] if "--ticker" in args else None
    ingest_statements(limit=limit, ticker=ticker)
