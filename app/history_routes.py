"""
New endpoints added to main.py for historical data.

Add these to your existing app/main.py:

  GET /api/companies/{ticker}/history     → 5yr price OHLCV
  GET /api/companies/{ticker}/financials  → 5yr P&L, BS, CF statements
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app import models

router = APIRouter(prefix="/api/companies")


@router.get("/{ticker}/history")
def price_history(ticker: str, db: Session = Depends(get_db)):
    """5 years of daily OHLCV prices."""
    co = db.query(models.Company).filter_by(ticker=ticker.upper()).first()
    if not co:
        raise HTTPException(404, f"Unknown ticker {ticker}")

    # Prefer 5yr historical prices
    hist = (db.query(models.HistoricalPrice)
              .filter_by(company_id=co.id)
              .order_by(models.HistoricalPrice.date)
              .all())

    if hist:
        return {
            "ticker": co.ticker,
            "source": "historical_prices",
            "count": len(hist),
            "data": [{"date":p.date,"open":p.open,"high":p.high,"low":p.low,"close":p.close,"volume":p.volume}
                     for p in hist],
        }

    # Fallback to 1yr PricePoint
    pts = sorted(co.prices, key=lambda x: x.t)
    return {
        "ticker": co.ticker,
        "source": "price_points",
        "count": len(pts),
        "data": [{"date": None, "open": None, "high": None, "low": None,
                  "close": p.close, "volume": None} for p in pts],
    }


@router.get("/{ticker}/financials")
def financial_history(ticker: str, db: Session = Depends(get_db)):
    """5 years of P&L, Balance Sheet and Cash Flow statements."""
    co = db.query(models.Company).filter_by(ticker=ticker.upper()).first()
    if not co:
        raise HTTPException(404, f"Unknown ticker {ticker}")

    rows = (db.query(models.HistoricalFinancial)
              .filter_by(company_id=co.id)
              .order_by(models.HistoricalFinancial.fiscal_year)
              .all())

    # Pivot into {year: {PL:{}, BS:{}, CF:{}}}
    years = {}
    for r in rows:
        if r.fiscal_year not in years:
            years[r.fiscal_year] = {"PL": {}, "BS": {}, "CF": {}}
        years[r.fiscal_year][r.statement_type][r.line_item] = r.value

    return {
        "ticker": co.ticker,
        "name": co.name,
        "type": co.type,
        "years_available": sorted(years.keys()),
        "statements": years,
    }
