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


@router.get("/{ticker}/insights")
def company_insights(ticker: str, db: Session = Depends(get_db)):
    """Analyst consensus, peer comps, price target, forward estimates, and
    Screener-style ratios/growth captured from IndianAPI v2. Returns an empty
    payload (has_data=False) rather than 404 when nothing's been ingested yet,
    so the frontend can render a graceful 'no data' state."""
    co = db.query(models.Company).filter_by(ticker=ticker.upper()).first()
    if not co:
        raise HTTPException(404, f"Unknown ticker {ticker}")

    row = db.query(models.CompanyInsight).filter_by(company_id=co.id).first()
    price = (co.market.price if co.market else None)

    if not row or not row.data:
        return {"ticker": co.ticker, "has_data": False, "price": price}

    data = dict(row.data)

    # Forward P/E, when forecast EPS is available in a recognisable shape.
    fwd_pe = None
    fwd_eps = _forward_eps(data.get("forecasts"))
    if fwd_eps and price:
        try:
            fwd_pe = round(price / fwd_eps, 2)
        except (ZeroDivisionError, TypeError):
            fwd_pe = None

    return {
        "ticker": co.ticker,
        "has_data": True,
        "price": price,
        "ticker_id": row.ticker_id,
        "forward_eps": fwd_eps,
        "forward_pe": fwd_pe,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        **data,
    }


def _forward_eps(forecasts):
    """Best-effort: pull the next forward annual EPS estimate from whatever
    shape /stock_forecasts returned. Returns None if not recognisable."""
    if not isinstance(forecasts, dict):
        return None
    eps = forecasts.get("eps")
    nums = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(v, (int, float)):
                    nums.append((str(k), float(v)))
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(eps)
    # Prefer a value tagged as a mean/estimate; else the largest plausible EPS.
    for key, val in nums:
        if any(t in key.lower() for t in ("mean", "estimate", "consensus")) and 0 < val < 100000:
            return val
    plausible = [v for _, v in nums if 0 < v < 100000]
    return max(plausible) if plausible else None


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
