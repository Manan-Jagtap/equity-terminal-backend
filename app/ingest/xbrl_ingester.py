"""
xbrl_ingester.py — pulls 5 years of real financial statement data.

THREE-TIER strategy (tries each in order, stops when data found):

  Tier 1 — BSE XBRL  (best: structured, deterministic, straight from filing)
           Downloads the XBRL ZIP from BSE's public corporate announcements
           for the last 5 annual results and parses the instance document.

  Tier 2 — Yahoo Finance financials
           Uses yfinance income_stmt / balance_sheet / cashflow
           (4 years of annual data, works for most large-caps).

  Tier 3 — Yahoo Finance info fields
           Back-calculates from ttm EPS, book value, revenue etc.
           (current year only — used when tiers 1 & 2 fail).

Run:
  python -m app.ingest.xbrl_ingester                    # all companies
  python -m app.ingest.xbrl_ingester --ticker MUTHOOTFIN  # one company
  python -m app.ingest.xbrl_ingester --tier yfinance    # skip BSE XBRL
"""
import os, sys, time, zipfile, io, requests, json, math
from datetime import datetime, timedelta, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import yfinance as yf
from sqlalchemy.orm import Session

from app.database import SessionLocal, engine
from app.models import Base, Company, HistoricalFinancial, HistoricalPrice, PricePoint, MarketSnapshot

Base.metadata.create_all(bind=engine)

# ─── BSE scrip code map (add more as needed) ────────────────────────────────
BSE_CODE = {
    "MUTHOOTFIN":"533398","MANAPPURAM":"531213","FEDFINA":"544010",
    "IIFL":"532636","BAJFINANCE":"500034","TITAN":"500114",
    "HDFCBANK":"500180","ICICIBANK":"532174","AXISBANK":"532215",
    "KOTAKBANK":"500247","SBIN":"500112","INFY":"500209",
    "TCS":"532540","RELIANCE":"500325","HINDUNILVR":"500696",
    "SUNPHARMA":"524715","WIPRO":"507685","HCLTECH":"532281",
    "BAJAJFINSV":"532978","TATAMOTORS":"500570","MARUTI":"532500",
    "ADANIENT":"512599","ADANIPORTS":"532921","NTPC":"532555",
    "POWERGRID":"532898","BHARTIARTL":"532454","ITC":"500875",
    "LT":"500510","ULTRACEMCO":"532538","DIVISLAB":"532488",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.bseindia.com/",
    "Accept": "application/json, text/plain, */*",
}

CR = 1e7  # Yahoo gives absolute INR


# ═══════════════════════════════════════════════════════════════════════════
# TIER 1 — BSE XBRL
# ═══════════════════════════════════════════════════════════════════════════

def _bse_get_result_filings(bse_code: str, max_years: int = 5) -> list:
    """
    Fetch list of annual result filings from BSE corporate announcements.
    Returns list of dicts with {year, attachment_url}.
    """
    url = (f"https://api.bseindia.com/BseIndiaAPI/api/AnnualReportMain/w?"
           f"scripcode={bse_code}&type=announcements&Category=Results")
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return []
        data = r.json()
        filings = []
        for item in data.get("Table", [])[:max_years * 2]:
            subject = item.get("NEWSSUB", "").lower()
            if "annual" in subject or "full year" in subject or "march" in subject:
                att = item.get("ATTACHMENTNAME", "")
                if att.endswith(".zip") or att.endswith(".xml"):
                    year = _extract_year(subject)
                    if year:
                        filings.append({"year": year, "url": att})
        return filings[:max_years]
    except Exception:
        return []


def _extract_year(text: str) -> Optional[int]:
    import re
    m = re.search(r"20(\d{2})", text)
    if m:
        return int("20" + m.group(1))
    return None


def _parse_xbrl_zip(url: str) -> dict:
    """Download XBRL ZIP, extract instance document, return {line_item: value}."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        if r.status_code != 200:
            return {}
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        # find the instance document (.xml, not _def or _lab or _pre)
        xml_files = [f for f in zf.namelist()
                     if f.endswith(".xml") and not any(x in f for x in ["_def","_lab","_pre","_cal"])]
        if not xml_files:
            return {}
        xml = zf.read(xml_files[0]).decode("utf-8", errors="ignore")
        return _extract_xbrl_values(xml)
    except Exception:
        return {}


def _extract_xbrl_values(xml: str) -> dict:
    """
    Very lightweight XBRL parser — extracts numeric values from tagged elements.
    Maps BSE taxonomy tags to our canonical line items.
    """
    import re
    TAG_MAP = {
        # P&L
        "Revenue":                      "revenue",
        "RevenueFromOperations":        "revenue",
        "TotalRevenue":                 "revenue",
        "NetSales":                     "revenue",
        "InterestIncome":               "interest_income",
        "NetInterestIncome":            "nii",
        "EBIT":                         "ebit",
        "ProfitBeforeTax":              "pbt",
        "TaxExpense":                   "tax",
        "ProfitAfterTax":               "pat",
        "ProfitLossForPeriod":          "pat",
        # Balance sheet
        "TotalAssets":                  "total_assets",
        "StockholdersEquity":           "equity",
        "TotalShareholdersEquity":      "equity",
        "Equity":                       "equity",
        "Borrowings":                   "borrowings",
        "LongTermBorrowings":           "lt_debt",
        "ShortTermBorrowings":          "st_debt",
        "CashAndCashEquivalents":       "cash",
        # Cash flow
        "NetCashFromOperatingActivities":   "operating_cf",
        "NetCashUsedInInvestingActivities": "investing_cf",
        "PurchaseOfFixedAssets":            "capex",
        "Dividends":                        "dividends",
        # NBFC specific
        "AssetsUnderManagement":        "aum",
        "GrossNPA":                     "gnpa_abs",
        "NetNPA":                       "nnpa_abs",
        "CapitalAdequacyRatio":         "crar",
        "NetInterestMargin":            "nim",
    }
    results = {}
    for tag, canonical in TAG_MAP.items():
        # match any namespace prefix: <ns:Revenue ...>value</ns:Revenue>
        pattern = rf"<[^>]*:{tag}[^>]*>([\d.,\-]+)<"
        m = re.search(pattern, xml, re.IGNORECASE)
        if m:
            try:
                val = float(m.group(1).replace(",",""))
                # BSE XBRL values are usually in INR units; divide by 1e7 for crores
                if val > 1e9:
                    val = val / 1e7
                results[canonical] = round(val, 2)
            except ValueError:
                pass
    return results


# ═══════════════════════════════════════════════════════════════════════════
# TIER 2 — Yahoo Finance annual statements
# ═══════════════════════════════════════════════════════════════════════════

def _yfinance_statements(yf_symbol: str) -> dict:
    """
    Pull 4 years of annual P&L, BS, CF from Yahoo Finance.
    Returns {fiscal_year: {"pl":{}, "bs":{}, "cf":{}}}
    """
    result = {}
    try:
        ticker = yf.Ticker(yf_symbol)

        # Income statement
        inc = ticker.income_stmt
        if inc is not None and not inc.empty:
            for col in inc.columns:
                fy = col.year
                if fy not in result:
                    result[fy] = {"pl": {}, "bs": {}, "cf": {}}
                pl = result[fy]["pl"]
                def g(row):
                    return round(inc.loc[row, col] / CR, 1) if row in inc.index and inc.loc[row, col] == inc.loc[row, col] else None
                pl["revenue"]   = g("Total Revenue")
                pl["gross"]     = g("Gross Profit")
                pl["ebit"]      = g("EBIT") or g("Operating Income")
                pl["ebitda"]    = g("EBITDA") or g("Normalized EBITDA")
                pl["pbt"]       = g("Pretax Income")
                pl["tax"]       = g("Tax Provision")
                pl["pat"]       = g("Net Income") or g("Net Income Common Stockholders")
                pl["interest"]  = g("Interest Expense")
                pl["depreciation"] = g("Reconciled Depreciation")

        # Balance sheet
        bs_df = ticker.balance_sheet
        if bs_df is not None and not bs_df.empty:
            for col in bs_df.columns:
                fy = col.year
                if fy not in result:
                    result[fy] = {"pl": {}, "bs": {}, "cf": {}}
                bs = result[fy]["bs"]
                def gb(row):
                    return round(bs_df.loc[row, col] / CR, 1) if row in bs_df.index and bs_df.loc[row, col] == bs_df.loc[row, col] else None
                bs["total_assets"]  = gb("Total Assets")
                bs["equity"]        = gb("Stockholders Equity") or gb("Common Stock Equity")
                bs["total_debt"]    = gb("Total Debt")
                bs["lt_debt"]       = gb("Long Term Debt")
                bs["cash"]          = gb("Cash And Cash Equivalents") or gb("Cash Cash Equivalents And Short Term Investments")
                bs["borrowings"]    = gb("Total Debt")

        # Cash flow
        cf_df = ticker.cashflow
        if cf_df is not None and not cf_df.empty:
            for col in cf_df.columns:
                fy = col.year
                if fy not in result:
                    result[fy] = {"pl": {}, "bs": {}, "cf": {}}
                cf = result[fy]["cf"]
                def gc(row):
                    return round(cf_df.loc[row, col] / CR, 1) if row in cf_df.index and cf_df.loc[row, col] == cf_df.loc[row, col] else None
                cf["operating_cf"]  = gc("Operating Cash Flow")
                cf["capex"]         = gc("Capital Expenditure")
                cf["fcf"]           = gc("Free Cash Flow")
                cf["dividends"]     = gc("Common Stock Dividend Paid") or gc("Dividends And Other Adjustments")

    except Exception as e:
        print(f"    yfinance statements error: {e}")

    return result


# ═══════════════════════════════════════════════════════════════════════════
# TIER 3 — Yahoo Finance info (current year fallback)
# ═══════════════════════════════════════════════════════════════════════════

def _yfinance_info_fallback(yf_symbol: str, shares_cr: float, current_fy: int) -> dict:
    """Derive current-year P&L from info fields when statements unavailable."""
    try:
        info = yf.Ticker(yf_symbol).info
        bv     = info.get("bookValue")
        eps    = info.get("trailingEps")
        rev    = info.get("totalRevenue")
        margin = info.get("operatingMargins")
        roa    = info.get("returnOnAssets")
        debt   = info.get("totalDebt", 0)
        cash   = info.get("totalCash", 0)
        assets = info.get("totalAssets")

        pl, bs, cf = {}, {}, {}
        if rev:    pl["revenue"] = round(rev / CR, 1)
        if eps and shares_cr: pl["pat"] = round(eps * shares_cr, 1)
        if rev and margin: pl["ebit"] = round(rev * margin / CR, 1)
        if bv and shares_cr: bs["equity"] = round(bv * shares_cr, 1)
        if debt:   bs["total_debt"] = round(debt / CR, 1)
        if cash:   bs["cash"] = round(cash / CR, 1)
        if assets: bs["total_assets"] = round(assets / CR, 1)
        if roa and assets: cf["operating_cf"] = round(roa * assets / CR * 1.3, 1)

        return {current_fy: {"pl": pl, "bs": bs, "cf": cf}}
    except Exception:
        return {}


# ═══════════════════════════════════════════════════════════════════════════
# 5-YEAR PRICE HISTORY
# ═══════════════════════════════════════════════════════════════════════════

def ingest_5yr_prices(db: Session, co: Company, yf_symbol: str) -> int:
    """Pull 5 years of daily OHLCV, store in historical_prices."""
    try:
        ticker = yf.Ticker(yf_symbol)
        hist = ticker.history(period="5y", interval="1d")
        if hist.empty:
            return 0

        # Delete existing 5yr history for this company
        db.query(HistoricalPrice).filter_by(company_id=co.id).delete()

        # Also refresh the 1yr PricePoint table (for technicals)
        cutoff = datetime.now(timezone.utc) - timedelta(days=365)
        db.query(PricePoint).filter_by(company_id=co.id).delete()

        rows_added = 0
        for i, (date, row) in enumerate(hist.iterrows()):
            close = round(float(row["Close"]), 2)
            if math.isnan(close):
                continue
            db.add(HistoricalPrice(
                company_id=co.id,
                date=date.strftime("%Y-%m-%d"),
                open=round(float(row["Open"]), 2) if not math.isnan(row["Open"]) else None,
                high=round(float(row["High"]), 2) if not math.isnan(row["High"]) else None,
                low=round(float(row["Low"]), 2)  if not math.isnan(row["Low"])  else None,
                close=close,
                volume=round(float(row["Volume"])) if not math.isnan(row["Volume"]) else None,
            ))
            # Also keep last 250 points in PricePoint for technicals
            if date > cutoff:
                db.add(PricePoint(company_id=co.id, t=i, close=close))
            rows_added += 1

        # Update market snapshot
        snap = co.market
        if snap:
            snap.price = hist["Close"].iloc[-1]
        else:
            db.add(MarketSnapshot(company_id=co.id, price=hist["Close"].iloc[-1]))

        db.commit()
        return rows_added

    except Exception as e:
        db.rollback()
        print(f"    Price history error: {e}")
        return 0


# ═══════════════════════════════════════════════════════════════════════════
# UPSERT HELPER
# ═══════════════════════════════════════════════════════════════════════════

def upsert_hist_fin(db: Session, company_id: int, fy: int,
                    stmt: str, item: str, value, source: str):
    if value is None:
        return
    existing = db.query(HistoricalFinancial).filter_by(
        company_id=company_id, fiscal_year=fy,
        statement_type=stmt, line_item=item
    ).first()
    if existing:
        # Only overwrite if upgrading source quality
        order = {"seed": 0, "yfinance": 1, "screener": 2, "xbrl": 3, "manual": 4}
        if order.get(source, 0) >= order.get(existing.source, 0):
            existing.value = value
            existing.source = source
    else:
        db.add(HistoricalFinancial(
            company_id=company_id, fiscal_year=fy,
            statement_type=stmt, line_item=item,
            value=value, source=source
        ))


def store_statements(db: Session, co: Company, year_data: dict, source: str):
    """Write {fy: {pl:{}, bs:{}, cf:{}}} into HistoricalFinancial."""
    for fy, stmts in year_data.items():
        for item, val in stmts.get("pl", {}).items():
            if val is not None:
                upsert_hist_fin(db, co.id, fy, "PL", item, val, source)
        for item, val in stmts.get("bs", {}).items():
            if val is not None:
                upsert_hist_fin(db, co.id, fy, "BS", item, val, source)
        for item, val in stmts.get("cf", {}).items():
            if val is not None:
                upsert_hist_fin(db, co.id, fy, "CF", item, val, source)
    db.commit()


# ═══════════════════════════════════════════════════════════════════════════
# MAIN INGESTER
# ═══════════════════════════════════════════════════════════════════════════

TICKER_MAP = {
    "MUTHOOTFIN":"MUTHOOTFIN.NS","MANAPPURAM":"MANAPPURAM.NS","FEDFINA":"FEDFINA.NS",
    "IIFL":"IIFL.NS","BAJFINANCE":"BAJFINANCE.NS","TITAN":"TITAN.NS",
    "HDFCBANK":"HDFCBANK.NS","ICICIBANK":"ICICIBANK.NS","AXISBANK":"AXISBANK.NS",
    "KOTAKBANK":"KOTAKBANK.NS","SBIN":"SBIN.NS","INFY":"INFY.NS","TCS":"TCS.NS",
    "RELIANCE":"RELIANCE.NS","HINDUNILVR":"HINDUNILVR.NS","SUNPHARMA":"SUNPHARMA.NS",
    "WIPRO":"WIPRO.NS","HCLTECH":"HCLTECH.NS","BAJAJFINSV":"BAJAJFINSV.NS",
    "TATAMOTORS":"TATAMOTORS.NS","MARUTI":"MARUTI.NS","NTPC":"NTPC.NS",
    "POWERGRID":"POWERGRID.NS","BHARTIARTL":"BHARTIARTL.NS","ITC":"ITC.NS",
    "LT":"LT.NS","ULTRACEMCO":"ULTRACEMCO.NS","DIVISLAB":"DIVISLAB.NS",
    "ADANIENT":"ADANIENT.NS","ADANIPORTS":"ADANIPORTS.NS",
}

# For companies not in TICKER_MAP, auto-generate the NSE symbol
def _yf_symbol(ticker: str) -> str:
    return TICKER_MAP.get(ticker, ticker + ".NS")


def ingest_company(db: Session, co: Company, tier: str = "all") -> str:
    """Ingest one company. Returns status string."""
    yf_sym = _yf_symbol(co.ticker)
    bse_code = BSE_CODE.get(co.ticker)
    current_fy = datetime.now().year if datetime.now().month >= 4 else datetime.now().year - 1
    year_data = {}
    source = "none"

    # ── Tier 1: BSE XBRL ──────────────────────────────────────────────────
    if tier in ("all", "xbrl") and bse_code:
        filings = _bse_get_result_filings(bse_code)
        for filing in filings:
            xbrl_data = _parse_xbrl_zip(filing["url"])
            if xbrl_data:
                fy = filing["year"]
                if fy not in year_data:
                    year_data[fy] = {"pl": {}, "bs": {}, "cf": {}}
                # Map XBRL fields to statements
                pl_map = {"revenue","nii","interest_income","ebit","pbt","tax","pat"}
                bs_map = {"total_assets","equity","borrowings","lt_debt","st_debt","cash"}
                cf_map = {"operating_cf","investing_cf","capex","dividends"}
                for k, v in xbrl_data.items():
                    if k in pl_map: year_data[fy]["pl"][k] = v
                    elif k in bs_map: year_data[fy]["bs"][k] = v
                    elif k in cf_map: year_data[fy]["cf"][k] = v
                source = "xbrl"

    # ── Tier 2: Yahoo Finance statements ──────────────────────────────────
    if tier in ("all", "yfinance") and len(year_data) < 3:
        yf_data = _yfinance_statements(yf_sym)
        if yf_data:
            for fy, stmts in yf_data.items():
                if fy not in year_data:
                    year_data[fy] = stmts
                else:
                    # Fill gaps from yfinance
                    for stmt_type in ("pl", "bs", "cf"):
                        for item, val in stmts.get(stmt_type, {}).items():
                            if item not in year_data[fy][stmt_type] and val is not None:
                                year_data[fy][stmt_type][item] = val
            source = "xbrl+yfinance" if source == "xbrl" else "yfinance"

    # ── Tier 3: Info fallback ──────────────────────────────────────────────
    if not year_data or current_fy not in year_data:
        fallback = _yfinance_info_fallback(yf_sym, co.shares_outstanding, current_fy)
        if fallback:
            year_data.update(fallback)
            source = source + "+info" if source != "none" else "info"

    # Store whatever we have
    if year_data:
        store_statements(db, co, year_data, source)

    # 5-year price history
    price_rows = ingest_5yr_prices(db, co, yf_sym)

    return f"stmts={len(year_data)}yrs(src={source}) prices={price_rows}"


def run(ticker_filter: str = None, tier: str = "all"):
    db = SessionLocal()
    try:
        query = db.query(Company)
        if ticker_filter:
            query = query.filter_by(ticker=ticker_filter.upper())
        companies = query.all()

        print(f"Ingesting {len(companies)} companies — statements + 5yr prices")
        print(f"Tier: {tier}  |  Pause: 6s between companies\n")

        ok = fail = 0
        for i, co in enumerate(companies, 1):
            try:
                status = ingest_company(db, co, tier)
                print(f"  [{i}/{len(companies)}] {co.ticker}: {status}")
                ok += 1
            except Exception as e:
                print(f"  [{i}/{len(companies)}] {co.ticker}: ERROR — {e}")
                fail += 1
            time.sleep(6)

        print(f"\nDone. OK={ok}  Failed={fail}")
    finally:
        db.close()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--ticker", help="Single ticker to ingest")
    p.add_argument("--tier", default="all", choices=["all","xbrl","yfinance","info"])
    args = p.parse_args()
    run(ticker_filter=args.ticker, tier=args.tier)
