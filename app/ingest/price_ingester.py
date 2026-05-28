"""
price_ingester.py — pulls live prices + 1yr OHLC from Yahoo Finance (free, no API key).

NSE tickers: append .NS  (e.g. MUTHOOTFIN.NS)
BSE tickers: append .BO  (e.g. 500271.BO)

Run manually:   python -m app.ingest.price_ingester
Run on Railway: add to Procfile or schedule via a cron job.
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import yfinance as yf
from sqlalchemy.orm import Session
from app.database import SessionLocal, engine
from app import models
from app.database import Base

Base.metadata.create_all(bind=engine)

# Map our ticker → Yahoo Finance symbol
TICKER_MAP = {
    "MUTHOOTFIN": "MUTHOOTFIN.NS",
    "MANAPPURAM":  "MANAPPURAM.NS",
    "FEDFINA":     "FEDFINA.NS",
    "IIFL":        "IIFL.NS",
    "BAJFINANCE":  "BAJFINANCE.NS",
    "TITAN":       "TITAN.NS",
}


def ingest_prices(db: Session):
    print("Starting price ingestion...")
    for our_ticker, yf_symbol in TICKER_MAP.items():
        co = db.query(models.Company).filter_by(ticker=our_ticker).first()
        if not co:
            print(f"  {our_ticker}: not in DB, skipping")
            continue
        try:
            stock = yf.Ticker(yf_symbol)
            info = stock.info

            # current price
            price = (info.get("currentPrice")
                     or info.get("regularMarketPrice")
                     or info.get("previousClose"))
            if not price:
                print(f"  {our_ticker}: no price returned, skipping")
                continue

            # upsert market snapshot
            snap = co.market
            if snap:
                snap.price = price
            else:
                db.add(models.MarketSnapshot(company_id=co.id, price=price))

            # historical OHLC — last 1 year, daily
            hist = stock.history(period="1y", interval="1d")
            if not hist.empty:
                # delete old price points
                db.query(models.PricePoint).filter_by(company_id=co.id).delete()
                for i, (date, row) in enumerate(hist.iterrows()):
                    db.add(models.PricePoint(
                        company_id=co.id,
                        t=i,
                        close=round(float(row["Close"]), 2)
                    ))
                print(f"  {our_ticker}: price={price:.2f}  {len(hist)} daily points loaded")
            else:
                print(f"  {our_ticker}: price={price:.2f}  (no history returned)")

            db.commit()
            time.sleep(1)  # be polite to Yahoo

        except Exception as e:
            db.rollback()
            print(f"  {our_ticker}: ERROR — {e}")

    print("Price ingestion complete.")


if __name__ == "__main__":
    db = SessionLocal()
    try:
        ingest_prices(db)
    finally:
        db.close()
