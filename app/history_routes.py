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


def _latest_facts(db: Session, company_id: int) -> dict:
    """Most recent value per concept from financial_facts."""
    rows = db.query(models.FinancialFact).filter_by(company_id=company_id).all()
    best = {}
    for r in rows:
        cur = best.get(r.concept)
        if cur is None or r.fiscal_year > cur[0]:
            best[r.concept] = (r.fiscal_year, r.value)
    return {k: v[1] for k, v in best.items()}


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
    """5 years of P&L, Balance Sheet and Cash Flow statements.

    Uses build_financials_response so the payload includes `has_data`, derived
    margins and CAGRs — the shape the frontend FinancialsTab renders from.
    """
    co = db.query(models.Company).filter_by(ticker=ticker.upper()).first()
    if not co:
        raise HTTPException(404, f"Unknown ticker {ticker}")

    rows = (db.query(models.HistoricalFinancial)
              .filter_by(company_id=co.id)
              .order_by(models.HistoricalFinancial.fiscal_year)
              .all())

    from app.financials import build_financials_response
    resp = build_financials_response(co, rows)
    resp["ticker"] = co.ticker
    resp["name"] = co.name
    resp["type"] = co.type
    return resp


@router.get("/{ticker}/metrics")
def company_metrics(ticker: str, category: str | None = None,
                    db: Session = Depends(get_db)):
    """80+ computed ratios & KPIs (Growth, Profitability, Returns, Leverage,
    Valuation, NBFC/Banking, …) for the Ratios & KPIs tab. Computed from the
    latest facts + multi-year statements via the metric registry."""
    co = db.query(models.Company).filter_by(ticker=ticker.upper()).first()
    if not co:
        raise HTTPException(404, f"Unknown ticker {ticker}")

    facts = _latest_facts(db, co.id)

    rows = (db.query(models.HistoricalFinancial)
              .filter_by(company_id=co.id)
              .order_by(models.HistoricalFinancial.fiscal_year)
              .all())
    from app.financials import build_financials_response
    fin = build_financials_response(co, rows)

    price = 0.0
    try:
        price = co.market.price if co.market else 0.0
    except Exception:
        price = 0.0

    template = getattr(co, "template_code", None) or "MANUFACTURING"

    from app.metrics import compute_metrics
    return compute_metrics(co, facts, fin.get("statements", {}), price,
                           template, category_filter=category)
