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

    # Forward EPS trajectory + next-FY forward P/E (confirmed Refinitiv shape).
    eps_est = _eps_estimates(data.get("forecasts"))
    fwd_eps = eps_est[0]["mean"] if eps_est else None
    fwd_eps_year = eps_est[0]["year"] if eps_est else None
    fwd_pe = None
    if fwd_eps and price:
        try:
            fwd_pe = round(price / fwd_eps, 2)
        except (ZeroDivisionError, TypeError):
            fwd_pe = None

    rev_est = _rev_estimates(data.get("forecasts"))

    return {
        "ticker": co.ticker,
        "has_data": True,
        "price": price,
        "ticker_id": row.ticker_id,
        "forward_eps": fwd_eps,
        "forward_eps_year": fwd_eps_year,
        "forward_pe": fwd_pe,
        "eps_estimates": eps_est,
        "rev_estimates": rev_est,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        **data,
    }


def _rev_estimates(forecasts):
    """Forward REVENUE estimates (FY+1, FY+2 …). Same Refinitiv shape as EPS but
    measure 'SAL'; values arrive in ₹ million → converted to ₹ crore (÷10)."""
    rev = (forecasts or {}).get("revenue")
    if not isinstance(rev, dict):
        return None
    out = []
    for p in (rev.get("periods") or []):
        num = (p.get("RelativePeriod") or {}).get("Number")
        if num is None or num < 1:
            continue
        inner = _estimate_inner(p)
        if not inner:
            continue
        try:
            mean = float(inner.get("Mean") or inner.get("UnverifiedMean"))
        except (TypeError, ValueError):
            continue
        if mean <= 0:
            continue
        year = (p.get("FiscalPeriod") or {}).get("Year")
        out.append({
            "year": year, "n_rel": num,
            "mean_cr": round(mean / 10.0),           # ₹ million → ₹ crore
            "high_cr": round(_safe_f(inner.get("High")) / 10.0) if _safe_f(inner.get("High")) else None,
            "low_cr": round(_safe_f(inner.get("Low")) / 10.0) if _safe_f(inner.get("Low")) else None,
        })
    out.sort(key=lambda e: e["n_rel"])
    return out or None


def _estimate_inner(period):
    """Return the inner Estimate dict from a forecast period, or None."""
    est = period.get("Estimates")
    if not isinstance(est, dict):
        return None
    arr = est.get("Estimate") or est.get("Estimates")
    if isinstance(arr, list) and arr:
        return arr[0] if isinstance(arr[0], dict) else None
    return arr if isinstance(arr, dict) else None


def _eps_estimates(forecasts):
    """Forward EPS estimates (FY+1, FY+2 …) from the Refinitiv-style forecast.
    Each forward period has RelativePeriod.Number >= 1 and an Estimates block
    with Mean/High/Low/NumberOfEstimates. Returns a year-sorted list of dicts;
    [0] is the nearest forward year (used for forward P/E)."""
    eps = (forecasts or {}).get("eps")
    if not isinstance(eps, dict):
        return None
    out = []
    for p in (eps.get("periods") or []):
        num = (p.get("RelativePeriod") or {}).get("Number")
        if num is None or num < 1:
            continue
        inner = _estimate_inner(p)
        if not inner:
            continue
        try:
            mean = float(inner.get("Mean") or inner.get("UnverifiedMean"))
        except (TypeError, ValueError):
            continue
        if mean <= 0:
            continue
        year = (p.get("FiscalPeriod") or {}).get("Year")
        out.append({
            "year": year, "n_rel": num, "mean": round(mean, 2),
            "high": _safe_f(inner.get("High")), "low": _safe_f(inner.get("Low")),
            "n_estimates": _safe_f(inner.get("NumberOfEstimates")),
        })
    out.sort(key=lambda e: e["n_rel"])
    return out or None


def _safe_f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


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
