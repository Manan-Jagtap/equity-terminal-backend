"""
New endpoints added to main.py for historical data.

Add these to your existing app/main.py:

  GET /api/companies/{ticker}/history     → 5yr price OHLCV
  GET /api/companies/{ticker}/financials  → 5yr P&L, BS, CF statements
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.price_hygiene import drop_bad_ticks
from app.database import get_db
from app import models

router = APIRouter(prefix="/api/companies")


def _actions_for(db: Session, company_id: int) -> list[dict]:
    """CorporateAction rows for a company as plain dicts (the shape
    app.corporate_actions expects)."""
    rows = db.query(models.CorporateAction).filter_by(company_id=company_id).all()
    return [{"action_type": a.action_type, "ex_date": a.ex_date,
             "value": a.value, "ratio": a.ratio} for a in rows]


def _pe_band(pe_history):
    """Valuation-band stats from a multi-year P/E series: where the CURRENT P/E
    sits within its own historical range (min / 25th / median / 75th / max +
    percentile rank). Returns None if too few points to be meaningful."""
    pts = [p for p in (pe_history or []) if isinstance(p, dict) and p.get("value") and p["value"] > 0]
    if len(pts) < 8:
        return None
    pts.sort(key=lambda p: str(p.get("date") or ""))
    cur = pts[-1]["value"]
    vals = sorted(p["value"] for p in pts)
    def q(f):
        return round(vals[min(len(vals) - 1, int(f * len(vals)))], 1)
    rank = sum(1 for v in vals if v <= cur) / len(vals)
    return {"current": round(cur, 1), "min": round(vals[0], 1), "p25": q(0.25),
            "median": q(0.5), "p75": q(0.75), "max": round(vals[-1], 1),
            "percentile": round(rank * 100), "n": len(vals)}


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
    """5 years of daily OHLCV prices, back-adjusted for splits & bonuses.

    Closes are stored as Dhan serves them — ALREADY split/bonus-adjusted by the
    vendor — so the line is continuous
    across corporate actions instead of showing a fake 50%/80% cliff. Dividends
    are NOT applied to the price line (they belong in total-return math)."""
    co = db.query(models.Company).filter_by(ticker=ticker.upper()).first()
    if not co:
        raise HTTPException(404, f"Unknown ticker {ticker}")

    actions = _actions_for(db, co.id)
    has_sb = any(a["action_type"] in ("split", "bonus") and a.get("ratio") for a in actions)

    # Prefer 5yr historical prices (dated → adjustable)
    hist = (db.query(models.HistoricalPrice)
              .filter_by(company_id=co.id)
              .order_by(models.HistoricalPrice.date)
              .all())

    if hist:
        raw = [{"date": p.date, "open": p.open, "high": p.high, "low": p.low,
                "close": p.close, "volume": p.volume} for p in hist]
        return {
            "ticker": co.ticker,
            "source": "historical_prices",
            "adjusted": has_sb,
            "count": len(raw),
            # Dhan's historical API serves SPLIT-ADJUSTED closes already —
            # applying our corporate-action ledger on top double-adjusted every
            # name with a split (fake x2-x10 up-cliffs at the ex-date, found by
            # the 500-name audit: CESC x10.45, COFORGE x5.07). Serve as-is,
            # minus V-spike bad ticks (vendor skips special-session candles
            # when adjusting — price_hygiene.drop_bad_ticks).
            "data": drop_bad_ticks(raw),
        }

    # Fallback to 1yr PricePoint (index-based, no dates → cannot back-adjust)
    pts = sorted(co.prices, key=lambda x: x.t)
    return {
        "ticker": co.ticker,
        "source": "price_points",
        "adjusted": False,
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
    # Data-integrity tie-outs (balance sheet balances, PAT reconciles, etc.) so
    # the UI can flag "numbers don't tie" instead of silently showing a bad one.
    try:
        from app.validation import validate_statements, validation_summary
        checks = validate_statements(resp.get("statements", {}), resp.get("is_financial", False))
        resp["validation"] = validation_summary(checks)
    except Exception:
        resp["validation"] = None
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

    # The company's OWN market multiples (P/E, P/B, ROE TTM, net margin, div
    # yield) — IndianAPI doesn't expose these on the company's page, but they
    # appear wherever the company is listed as another company's peer. We build
    # one global map from all peer lists so the same numbers show everywhere.
    self_metrics = _peer_metrics_map(db).get(row.ticker_id) if row.ticker_id else None

    return {
        "ticker": co.ticker,
        "has_data": True,
        "price": price,
        "ticker_id": row.ticker_id,
        "self_metrics": self_metrics,
        "pe_band": _pe_band(data.get("pe_history")),
        "forward_eps": fwd_eps,
        "forward_eps_year": fwd_eps_year,
        "forward_pe": fwd_pe,
        "eps_estimates": eps_est,
        "rev_estimates": rev_est,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        **data,
    }


# Global peer-metrics map: ticker_id → {pe, pb, roe_ttm, npm_ttm, div_yield,
# price, rating, name}, assembled from every company's stored peer list. Cached
# for an hour. Lets a company display the SAME IndianAPI multiples it shows when
# it appears as someone else's peer (single source of truth).
_PEER_MAP = None
_PEER_MAP_TS = 0.0


def _peer_metrics_map(db):
    global _PEER_MAP, _PEER_MAP_TS
    import time as _t
    if _PEER_MAP is not None and (_t.time() - _PEER_MAP_TS) < 3600:
        return _PEER_MAP
    m = {}
    for row in db.query(models.CompanyInsight).all():
        for p in ((row.data or {}).get("peers") or []):
            tid = p.get("ticker_id")
            if tid and tid not in m:
                m[tid] = {k: p.get(k) for k in
                          ("pe", "pb", "roe_ttm", "npm_ttm", "div_yield", "price", "rating", "name")}
    _PEER_MAP, _PEER_MAP_TS = m, _t.time()
    return m


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
        hi = _safe_f(inner.get("High"))
        lo = _safe_f(inner.get("Low"))
        out.append({
            "year": year, "n_rel": num,
            "mean_cr": round(mean / 10.0),           # ₹ million → ₹ crore
            "high_cr": round(hi / 10.0) if hi else None,
            "low_cr": round(lo / 10.0) if lo else None,
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


@router.get("/{ticker}/forensics")
def company_forensics(ticker: str, db: Session = Depends(get_db)):
    """Accounting-quality / forensic red-flags computed from the company's own
    stored statements: adapted Piotroski F, Sloan accrual ratio, cash conversion,
    interest coverage, net-debt/EBITDA and leverage trend, with a 0-100 composite
    + red/amber/green flags. Classic Beneish M / Altman Z'' are listed under
    `pending` until the ingester captures receivables / working capital / SG&A.
    Never 404s on thin data — returns available=False with a reason instead."""
    co = db.query(models.Company).filter_by(ticker=ticker.upper()).first()
    if not co:
        raise HTTPException(404, f"Unknown ticker {ticker}")

    rows = (db.query(models.HistoricalFinancial)
              .filter_by(company_id=co.id)
              .order_by(models.HistoricalFinancial.fiscal_year)
              .all())
    # Forensics reads the FULL set of stored line items (receivables, current
    # assets/liabilities, gross PPE, accumulated depreciation, …) — not the
    # curated financials pull list — so Beneish / Altman inputs flow through.
    statements: dict = {}
    for r in rows:
        if r.value is None:
            continue
        statements.setdefault(int(r.fiscal_year), {}).setdefault(r.statement_type, {})[r.line_item] = r.value

    from app.forensics import forensic_report
    report = forensic_report(statements, {"type": co.type})
    report["ticker"] = co.ticker
    report["name"] = co.name
    return report


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
