"""
price_ingester.py — refresh current prices (and optionally 1yr OHLC) from Yahoo.

FIX: previously this only refreshed a hard-coded 6-ticker map, so every other
company showed a stale price from the original bulk load. It now refreshes the
current price for EVERY company in the DB (or a ticker subset), using yfinance's
fast quote with a history fallback. Batched + resilient (per-company commit).

Note on freshness: Yahoo gives the last traded / latest close — current but not
tick-by-tick real-time. Good enough for a fundamentals terminal.

Run:
  python -m app.ingest.price_ingester                 # all companies, price only
  python -m app.ingest.price_ingester --history       # also refresh 1yr OHLC
  python -m app.ingest.price_ingester --ticker TCS    # one company
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import yfinance as yf
from sqlalchemy.orm import Session
from app.database import SessionLocal, engine, Base
from app import models

Base.metadata.create_all(bind=engine)


def _current_price(tk):
    """Latest price via fast_info, falling back to the most recent daily close."""
    try:
        p = tk.fast_info.last_price
        if p:
            return float(p)
    except Exception:
        pass
    try:
        h = tk.history(period="1d")
        if not h.empty:
            return float(h["Close"].iloc[-1])
    except Exception:
        pass
    return None


def ingest_prices(db: Session, with_history=False, ticker=None, limit=None):
    q = db.query(models.Company)
    if ticker:
        q = q.filter(models.Company.ticker == ticker.upper())
    companies = q.all()
    if limit:
        companies = companies[:limit]

    print(f"Refreshing prices for {len(companies)} companies "
          f"({'with' if with_history else 'no'} OHLC history)...")
    ok = 0
    for co in companies:
        sym = (co.ticker or "").upper()
        if not sym:
            continue
        try:
            tk = yf.Ticker(sym + ".NS")
            price = _current_price(tk)
            if not price:
                print(f"  {co.ticker}: no price")
                continue
            price = round(price, 2)

            if co.market:
                co.market.price = price
            else:
                db.add(models.MarketSnapshot(company_id=co.id, price=price))

            if with_history:
                hist = tk.history(period="1y", interval="1d")
                if not hist.empty:
                    db.query(models.PricePoint).filter_by(company_id=co.id).delete()
                    for i, (_, row) in enumerate(hist.iterrows()):
                        db.add(models.PricePoint(company_id=co.id, t=i,
                                                 close=round(float(row["Close"]), 2)))

            db.commit()
            ok += 1
        except Exception as e:
            db.rollback()
            print(f"  {co.ticker}: ERROR — {type(e).__name__}")
        time.sleep(0.2)  # be polite to Yahoo

    print(f"Price refresh complete. {ok}/{len(companies)} updated.")


if __name__ == "__main__":
    args = sys.argv[1:]
    with_history = "--history" in args
    ticker = args[args.index("--ticker") + 1] if "--ticker" in args else None
    limit = int(args[args.index("--limit") + 1]) if "--limit" in args else None
    db = SessionLocal()
    try:
        ingest_prices(db, with_history=with_history, ticker=ticker, limit=limit)
    finally:
        db.close()
