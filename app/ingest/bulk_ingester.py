"""
bulk_ingester.py — adds all Nifty 500 companies to your platform.
Fixed: creates a fresh DB connection per company to avoid Railway timeout.

Run:
  python -m app.ingest.bulk_ingester --nifty50   # 50 companies (~5 mins)
  python -m app.ingest.bulk_ingester --index nifty500  # 500 companies (~45 mins)

Re-run safely — already-ingested companies are skipped.
"""
import os, sys, time, io, requests, csv
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import yfinance as yf
from app.database import SessionLocal, engine
from app import models, concepts as K
from app.database import Base

Base.metadata.create_all(bind=engine)

INDEX_URLS = {
    "nifty50":  "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv",
    "nifty100": "https://nsearchives.nseindia.com/content/indices/ind_nifty100list.csv",
    "nifty500": "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
}

NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.nseindia.com/",
}

FINANCIAL_SECTORS = {
    "financial services", "bank", "insurance", "nbfc",
    "housing finance", "microfinance", "diversified financials",
    "capital markets", "consumer finance", "financial"
}

CR = 1e7

def is_financial(industry):
    return any(s in industry.lower() for s in FINANCIAL_SECTORS)

def derive_financial_assumptions(info):
    roe = info.get("returnOnEquity")
    payout_ratio = info.get("payoutRatio")
    if roe and 0.02 < roe < 0.60:
        forecast_roe = max(0.08, min(roe, 0.30))
    else:
        forecast_roe = 0.150
    terminal_roe = max(0.10, min(round(0.40 * forecast_roe + 0.60 * 0.135, 4), 0.18))
    payout = payout_ratio if (payout_ratio and 0 <= payout_ratio < 0.95) else 0.20
    return dict(beta=1.1, risk_free=0.069, erp=0.065,
                forecast_roe=round(forecast_roe, 4), terminal_roe=terminal_roe,
                payout=round(payout, 4), fade_years=8, terminal_growth=0.05,
                rev_growth=None, ebit_margin=None, tax_rate=None,
                reinvest_rate=None, debt_weight=None, cost_debt=None)


def default_assumptions(company_type, sector, info=None):
    if company_type == "financial":
        if info is not None:
            return derive_financial_assumptions(info)
        return dict(beta=1.1, risk_free=0.069, erp=0.065,
                    forecast_roe=0.155, terminal_roe=0.130,
                    payout=0.20, fade_years=8, terminal_growth=0.05,
                    rev_growth=None, ebit_margin=None, tax_rate=None,
                    reinvest_rate=None, debt_weight=None, cost_debt=None)
    s = sector.lower()
    if "technology" in s or "it" in s: margin, growth = 0.22, 0.14
    elif "pharma" in s or "health" in s: margin, growth = 0.18, 0.12
    elif "consumer" in s or "fmcg" in s: margin, growth = 0.16, 0.10
    elif "auto" in s: margin, growth = 0.12, 0.10
    elif "energy" in s or "oil" in s: margin, growth = 0.14, 0.07
    elif "cement" in s: margin, growth = 0.18, 0.09
    else: margin, growth = 0.14, 0.10
    return dict(beta=1.0, risk_free=0.069, erp=0.065,
                ebit_margin=margin, tax_rate=0.25, reinvest_rate=0.35,
                rev_growth=growth, debt_weight=0.20, cost_debt=0.085,
                fade_years=8, terminal_growth=0.055,
                forecast_roe=None, terminal_roe=None, payout=None)

def fetch_nse_list(index="nifty500"):
    url = INDEX_URLS.get(index, INDEX_URLS["nifty500"])
    print(f"Downloading {index} list from NSE...")
    try:
        r = requests.get(url, headers=NSE_HEADERS, timeout=20)
        if r.status_code != 200:
            print(f"  NSE returned {r.status_code}. Using Nifty 50 fallback.")
            return NIFTY50_FALLBACK
        reader = csv.DictReader(io.StringIO(r.text))
        rows = [(row.get("Company Name","").strip(),
                 row.get("Symbol","").strip(),
                 row.get("Industry","").strip())
                for row in reader
                if row.get("Symbol","").strip()]
        print(f"  Downloaded {len(rows)} companies.")
        return rows
    except Exception as e:
        print(f"  Error: {e}. Using fallback.")
        return NIFTY50_FALLBACK

def ingest_company(name, symbol, industry, batch_num, total):
    """Fresh DB session per company — avoids Railway connection timeout."""
    yf_symbol = symbol + ".NS"
    company_type = "financial" if is_financial(industry) else "nonfinancial"

    # fresh connection every time
    db = SessionLocal()
    try:
        # skip if already exists
        if db.query(models.Company).filter_by(ticker=symbol).first():
            print(f"  [{batch_num}/{total}] {symbol}: already in DB, skipping")
            return "skipped"

        info = yf.Ticker(yf_symbol).info
        price = (info.get("currentPrice") or info.get("regularMarketPrice")
                 or info.get("previousClose"))
        if not price:
            print(f"  [{batch_num}/{total}] {symbol}: no price, skipping")
            return "failed"

        shares = info.get("sharesOutstanding", 0)
        shares_cr = (shares / CR) if shares else (info.get("marketCap", 0) / price / CR)
        if shares_cr < 0.01:
            print(f"  [{batch_num}/{total}] {symbol}: bad shares, skipping")
            return "failed"

        co = models.Company(ticker=symbol, name=name, type=company_type,
                            sector=industry, shares_outstanding=round(shares_cr, 2))
        db.add(co); db.flush()

        bv = info.get("bookValue")
        if bv:
            db.add(models.FinancialFact(company_id=co.id, fiscal_year=2026,
                period="FY", concept=K.NET_WORTH,
                value=round(bv * shares_cr, 1), unit="INR_CR", source="yfinance"))

        eps = info.get("trailingEps")
        if eps:
            db.add(models.FinancialFact(company_id=co.id, fiscal_year=2026,
                period="FY", concept=K.NET_PROFIT,
                value=round(eps * shares_cr, 1), unit="INR_CR", source="yfinance"))

        if company_type == "nonfinancial":
            rev = info.get("totalRevenue")
            if rev:
                db.add(models.FinancialFact(company_id=co.id, fiscal_year=2026,
                    period="FY", concept=K.REVENUE,
                    value=round(rev / CR, 1), unit="INR_CR", source="yfinance"))
            debt = info.get("totalDebt") or 0
            cash = info.get("totalCash") or 0
            db.add(models.FinancialFact(company_id=co.id, fiscal_year=2026,
                period="FY", concept=K.NET_DEBT,
                value=round((debt - cash) / CR, 1), unit="INR_CR", source="yfinance"))

        asm = default_assumptions(company_type, industry, info)
        db.add(models.Assumptions(company_id=co.id, **asm))
        db.add(models.MarketSnapshot(company_id=co.id, price=price))

        hist = yf.Ticker(yf_symbol).history(period="1y", interval="1d")
        if not hist.empty:
            for i, (_, row) in enumerate(hist.iterrows()):
                db.add(models.PricePoint(company_id=co.id, t=i,
                                         close=round(float(row["Close"]), 2)))

        db.commit()
        pts = len(hist) if not hist.empty else 0
        print(f"  [{batch_num}/{total}] {symbol}: price=Rs{price:.0f}  {company_type}  {pts}pts")
        return "added"

    except Exception as e:
        try: db.rollback()
        except: pass
        print(f"  [{batch_num}/{total}] {symbol}: ERROR — {e}")
        return "failed"
    finally:
        db.close()  # always close — fresh connection next time

def run(index="nifty500"):
    companies = fetch_nse_list(index)
    total = len(companies)
    added = skipped = failed = 0
    print(f"\nIngesting {total} companies (~{total*6//60} mins). Ctrl+C to pause and resume.\n")
    try:
        for i, (name, symbol, industry) in enumerate(companies, 1):
            r = ingest_company(name, symbol, industry, i, total)
            if r == "added": added += 1
            elif r == "skipped": skipped += 1
            else: failed += 1
            time.sleep(5)
    except KeyboardInterrupt:
        print("\nPaused. Re-run to continue — already-added companies are skipped.")
    print(f"\nAdded: {added}  Skipped: {skipped}  Failed: {failed}")

NIFTY50_FALLBACK = [
    ("Adani Enterprises Ltd.", "ADANIENT", "Metals & Mining"),
    ("Adani Ports & SEZ Ltd.", "ADANIPORTS", "Transportation"),
    ("Apollo Hospitals Enterprise Ltd.", "APOLLOHOSP", "Healthcare"),
    ("Asian Paints Ltd.", "ASIANPAINT", "Consumer Goods"),
    ("Axis Bank Ltd.", "AXISBANK", "Financial Services"),
    ("Bajaj Auto Ltd.", "BAJAJ-AUTO", "Automobile"),
    ("Bajaj Finance Ltd.", "BAJFINANCE", "Financial Services"),
    ("Bajaj Finserv Ltd.", "BAJAJFINSV", "Financial Services"),
    ("BEL", "BEL", "Capital Goods"),
    ("Bharti Airtel Ltd.", "BHARTIARTL", "Telecommunication"),
    ("Cipla Ltd.", "CIPLA", "Pharmaceuticals"),
    ("Coal India Ltd.", "COALINDIA", "Oil Gas"),
    ("Dr Reddys Laboratories Ltd.", "DRREDDY", "Pharmaceuticals"),
    ("Eicher Motors Ltd.", "EICHERMOT", "Automobile"),
    ("Zomato Ltd.", "ETERNAL", "Consumer Services"),
    ("Grasim Industries Ltd.", "GRASIM", "Diversified"),
    ("HCL Technologies Ltd.", "HCLTECH", "Information Technology"),
    ("HDFC Bank Ltd.", "HDFCBANK", "Financial Services"),
    ("HDFC Life Insurance Company Ltd.", "HDFCLIFE", "Financial Services"),
    ("Hindalco Industries Ltd.", "HINDALCO", "Metals & Mining"),
    ("Hindustan Unilever Ltd.", "HINDUNILVR", "Fast Moving Consumer Goods"),
    ("ICICI Bank Ltd.", "ICICIBANK", "Financial Services"),
    ("IndusInd Bank Ltd.", "INDUSINDBK", "Financial Services"),
    ("Infosys Ltd.", "INFY", "Information Technology"),
    ("ITC Ltd.", "ITC", "Fast Moving Consumer Goods"),
    ("JSW Steel Ltd.", "JSWSTEEL", "Metals & Mining"),
    ("Kotak Mahindra Bank Ltd.", "KOTAKBANK", "Financial Services"),
    ("Larsen & Toubro Ltd.", "LT", "Construction"),
    ("Mahindra & Mahindra Ltd.", "M&M", "Automobile"),
    ("Maruti Suzuki India Ltd.", "MARUTI", "Automobile"),
    ("Nestle India Ltd.", "NESTLEIND", "Fast Moving Consumer Goods"),
    ("NTPC Ltd.", "NTPC", "Power"),
    ("ONGC", "ONGC", "Oil Gas"),
    ("Power Grid Corporation Ltd.", "POWERGRID", "Power"),
    ("Reliance Industries Ltd.", "RELIANCE", "Oil Gas"),
    ("SBI Life Insurance Co. Ltd.", "SBILIFE", "Financial Services"),
    ("Shriram Finance Ltd.", "SHRIRAMFIN", "Financial Services"),
    ("State Bank of India", "SBIN", "Financial Services"),
    ("Sun Pharmaceutical Industries Ltd.", "SUNPHARMA", "Pharmaceuticals"),
    ("Tata Consultancy Services Ltd.", "TCS", "Information Technology"),
    ("Tata Consumer Products Ltd.", "TATACONSUM", "Fast Moving Consumer Goods"),
    ("Tata Motors Ltd.", "TATAMOTORS", "Automobile"),
    ("Tata Steel Ltd.", "TATASTEEL", "Metals & Mining"),
    ("Tech Mahindra Ltd.", "TECHM", "Information Technology"),
    ("Titan Company Ltd.", "TITAN", "Consumer Durables"),
    ("Trent Ltd.", "TRENT", "Consumer Durables"),
    ("UltraTech Cement Ltd.", "ULTRACEMCO", "Cement"),
    ("Wipro Ltd.", "WIPRO", "Information Technology"),
    ("Zomato Ltd.", "ZOMATO", "Consumer Services"),
    ("Swiggy Ltd.", "SWIGGY", "Consumer Services"),
]

if __name__ == "__main__":
    idx = "nifty500"
    if "--nifty50" in sys.argv: idx = "nifty50"
    elif "--index" in sys.argv:
        pos = sys.argv.index("--index")
        if pos + 1 < len(sys.argv): idx = sys.argv[pos + 1]
    run(idx)
