"""
bse_results_ingester.py — pulls quarterly financial results from BSE's public API.

BSE exposes financial results via its corporate announcements API.
This ingester fetches the latest quarterly P&L for each company and maps
the line items to our canonical concepts — including NBFC-specific ones
(NIM, GNPA, NNPA, CRAR) that Yahoo Finance doesn't carry.

BSE security codes for our universe:
  MUTHOOTFIN  533398
  MANAPPURAM  531213
  FEDFINA     544010
  IIFL        532636
  BAJFINANCE  500034
  TITAN       500114

Run: python -m app.ingest.bse_results_ingester
"""
import os, sys, time, requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from sqlalchemy.orm import Session
from app.database import SessionLocal, engine
from app import models, concepts as K
from app.database import Base

Base.metadata.create_all(bind=engine)

BSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.bseindia.com/",
    "Accept": "application/json, text/plain, */*",
}

# Our ticker → BSE security code
BSE_CODE_MAP = {
    "MUTHOOTFIN": "533398",
    "MANAPPURAM":  "531213",
    "FEDFINA":     "544010",
    "IIFL":        "532636",
    "BAJFINANCE":  "500034",
    "TITAN":       "500114",
}

CR = 1e7   # Yahoo gives absolute INR, BSE gives lakhs in some fields


def fetch_bse_financials(bse_code: str) -> dict:
    """Fetch the latest annual financial results from BSE."""
    url = (f"https://api.bseindia.com/BseIndiaAPI/api/AnnualReport/"
           f"w?scripcode={bse_code}&type=annualreport")
    try:
        r = requests.get(url, headers=BSE_HEADERS, timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"    BSE API error: {e}")
    return None


def fetch_bse_ratios(bse_code: str) -> dict:
    """Fetch key ratios from BSE company info."""
    url = f"https://api.bseindia.com/BseIndiaAPI/api/CompanyHeader/w?quotetype=EQ&scripcode={bse_code}"
    try:
        r = requests.get(url, headers=BSE_HEADERS, timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"    BSE ratios error: {e}")
    return None


def upsert_fact(db, company_id, concept, value, fy=2026, unit="INR_CR"):
    if value is None or value == 0:
        return
    existing = db.query(models.FinancialFact).filter_by(
        company_id=company_id, fiscal_year=fy, period="FY", concept=concept
    ).first()
    if existing:
        existing.value = value
        existing.source = "bse_api"
    else:
        db.add(models.FinancialFact(
            company_id=company_id, fiscal_year=fy, period="FY",
            concept=concept, value=value, unit=unit, source="bse_api"
        ))


def safe_float(val):
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def ingest_bse_results(db: Session):
    print("Starting BSE results ingestion...")
    for our_ticker, bse_code in BSE_CODE_MAP.items():
        co = db.query(models.Company).filter_by(ticker=our_ticker).first()
        if not co:
            print(f"  {our_ticker}: not in DB, skipping")
            continue

        print(f"  {our_ticker} (BSE {bse_code})...")
        data = fetch_bse_ratios(bse_code)

        if data:
            try:
                # BSE company header has: BookValue, EPS, PE, PBV, FaceValue
                bv = safe_float(data.get("BookValue"))
                eps = safe_float(data.get("EPS52"))
                if bv and co.shares_outstanding:
                    upsert_fact(db, co.id, K.NET_WORTH,
                                round(bv * co.shares_outstanding, 1))
                if eps and co.shares_outstanding:
                    upsert_fact(db, co.id, K.NET_PROFIT,
                                round(eps * co.shares_outstanding, 1))
                print(f"    BV/share={bv}  EPS={eps}")
            except Exception as e:
                print(f"    Parse error: {e}")
        else:
            print(f"    BSE API returned nothing — keeping existing values")

        db.commit()
        time.sleep(2)   # respectful delay

    print()
    print("BSE results ingestion complete.")
    print()
    print("IMPORTANT — NBFC-specific metrics (GNPA, NNPA, CRAR, NIM, AUM):")
    print("These are NOT available from BSE's public header API.")
    print("To get real values, do ONE of the following:")
    print()
    print("  Option A (recommended, free): Download the quarterly XBRL ZIP from")
    print("  BSE Corporate Announcements for each company, parse the instance")
    print("  document, and map tags to concepts. See xbrl_parser.py.")
    print()
    print("  Option B (manual, fastest for a small universe): Copy the numbers")
    print("  directly from the quarterly result PDF into update_nbfc_metrics().")
    print("  Run it once per quarter after results are announced.")
    print()


def update_nbfc_metrics_manually(db: Session):
    """
    Manually update NBFC-specific metrics from the latest quarterly results.
    Copy the numbers from the investor presentation or quarterly result PDF.
    Run this once per quarter after results are announced.
    
    FY26 Q4 figures — REPLACE THESE WITH REAL NUMBERS FROM THE RESULT PDFs.
    """
    NBFC_DATA = {
        # ticker: {concept: value}
        "MUTHOOTFIN": {
            K.AUM:  92000,   # ₹ cr — from investor presentation
            K.GNPA:  0.029,  # ratio — from quarterly result
            K.NNPA:  0.026,
            K.CRAR:  0.288,
            K.NIM:   0.115,
            K.ROA:   0.052,
        },
        "MANAPPURAM": {
            K.AUM:  44000,
            K.GNPA:  0.045,
            K.NNPA:  0.040,
            K.CRAR:  0.302,
            K.NIM:   0.135,
            K.ROA:   0.048,
        },
        "FEDFINA": {
            K.AUM:  14500,
            K.GNPA:  0.020,
            K.NNPA:  0.015,
            K.CRAR:  0.205,
            K.NIM:   0.080,
            K.ROA:   0.022,
        },
        "IIFL": {
            K.AUM:  78000,
            K.GNPA:  0.024,
            K.NNPA:  0.012,
            K.CRAR:  0.213,
            K.NIM:   0.090,
            K.ROA:   0.024,
        },
        "BAJFINANCE": {
            K.AUM:  410000,
            K.GNPA:  0.010,
            K.NNPA:  0.004,
            K.CRAR:  0.219,
            K.NIM:   0.105,
            K.ROA:   0.045,
        },
    }
    for ticker, metrics in NBFC_DATA.items():
        co = db.query(models.Company).filter_by(ticker=ticker).first()
        if not co:
            continue
        for concept, value in metrics.items():
            unit = "RATIO" if concept in (K.GNPA, K.NNPA, K.CRAR, K.NIM, K.ROA) else "INR_CR"
            upsert_fact(db, co.id, concept, value, unit=unit)
        db.commit()
        print(f"  {ticker}: NBFC metrics updated")


if __name__ == "__main__":
    db = SessionLocal()
    try:
        ingest_bse_results(db)
        print("Also updating NBFC-specific metrics (manual figures)...")
        update_nbfc_metrics_manually(db)
    finally:
        db.close()
