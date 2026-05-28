"""
fundamentals_ingester.py — pulls key financial metrics from Yahoo Finance.

Yahoo Finance's `info` dict contains:
  - bookValue, returnOnEquity, trailingEps, totalDebt, totalRevenue, operatingMargins
  - For NBFCs these are approximations — supplement with BSE quarterly results.

This gives you a working baseline immediately. Replace individual fields
with XBRL-sourced values as you build the XBRL ingester.

Run: python -m app.ingest.fundamentals_ingester
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import yfinance as yf
from sqlalchemy.orm import Session
from app.database import SessionLocal, engine
from app import models, concepts as K
from app.database import Base

Base.metadata.create_all(bind=engine)

TICKER_MAP = {
    "MUTHOOTFIN": "MUTHOOTFIN.NS",
    "MANAPPURAM":  "MANAPPURAM.NS",
    "FEDFINA":     "FEDFINA.NS",
    "IIFL":        "IIFL.NS",
    "BAJFINANCE":  "BAJFINANCE.NS",
    "TITAN":       "TITAN.NS",
}

# crore conversion: Yahoo gives absolute INR values
CR = 1e7


def upsert_fact(db, company_id, concept, value, fy=2026, unit="INR_CR"):
    """Insert or update a single FinancialFact row."""
    if value is None:
        return
    existing = db.query(models.FinancialFact).filter_by(
        company_id=company_id, fiscal_year=fy, period="FY", concept=concept
    ).first()
    if existing:
        existing.value = value
        existing.source = "yfinance"
    else:
        db.add(models.FinancialFact(
            company_id=company_id, fiscal_year=fy, period="FY",
            concept=concept, value=value, unit=unit, source="yfinance"
        ))


def ingest_fundamentals(db: Session):
    print("Starting fundamentals ingestion...")
    for our_ticker, yf_symbol in TICKER_MAP.items():
        co = db.query(models.Company).filter_by(ticker=our_ticker).first()
        if not co:
            print(f"  {our_ticker}: not in DB, skipping")
            continue
        try:
            info = yf.Ticker(yf_symbol).info
            shares = co.shares_outstanding  # crore

            # --- Common ---
            # Book value: Yahoo gives per-share in INR → multiply by shares (cr) → cr
            bv_per_share = info.get("bookValue")
            if bv_per_share:
                net_worth_cr = bv_per_share * shares
                upsert_fact(db, co.id, K.NET_WORTH, round(net_worth_cr, 1))

            # Net profit: trailingEps * shares (cr) → cr
            eps = info.get("trailingEps")
            if eps:
                net_profit_cr = eps * shares
                upsert_fact(db, co.id, K.NET_PROFIT, round(net_profit_cr, 1))

            # --- Non-financials ---
            if co.type == "nonfinancial":
                rev = info.get("totalRevenue")
                if rev:
                    upsert_fact(db, co.id, K.REVENUE, round(rev / CR, 1))
                total_debt = info.get("totalDebt") or 0
                cash = info.get("totalCash") or 0
                upsert_fact(db, co.id, K.NET_DEBT, round((total_debt - cash) / CR, 1))

            # --- Financials (approximate from Yahoo) ---
            if co.type == "financial":
                roe = info.get("returnOnEquity")
                if roe and bv_per_share:
                    # ROA ≈ ROE × (equity/assets) — Yahoo doesn't give assets directly
                    # Use returnOnAssets if available
                    roa = info.get("returnOnAssets")
                    if roa:
                        upsert_fact(db, co.id, K.ROA, round(roa, 4), unit="RATIO")

                # NIM not in Yahoo — keep seed value unless you have XBRL
                # GNPA / NNPA / CRAR not in Yahoo — keep seed values

            db.commit()
            print(f"  {our_ticker}: fundamentals updated from Yahoo Finance")
            time.sleep(1)

        except Exception as e:
            db.rollback()
            print(f"  {our_ticker}: ERROR — {e}")

    print("Fundamentals ingestion complete.")
    print()
    print("NOTE: GNPA, NNPA, CRAR, NIM are NBFC-specific and not available")
    print("on Yahoo Finance. These still use seed values until you add the")
    print("BSE quarterly results ingester (next step).")


if __name__ == "__main__":
    db = SessionLocal()
    try:
        ingest_fundamentals(db)
    finally:
        db.close()
