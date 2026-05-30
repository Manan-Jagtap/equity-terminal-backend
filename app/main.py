"""
FastAPI application — fixed to return real financial data from DB.

Key fix: /api/companies now returns shares_outstanding, equity, net_profit,
revenue, net_debt so the frontend can build accurate DCF models instead
of back-calculating from ratios.
"""
import os
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from .database import Base, engine, get_db
from . import models, engines
from .assemble import build_company, assumptions_dict
from .schemas import AssumptionOverride
from . import concepts as K

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Equity Research Terminal API", version="2.0")

from app.history_routes import router as history_router
app.include_router(history_router)
from app.news_routes import router as news_router
app.include_router(news_router)
from app.onepager import build_onepager

origins = os.getenv("FRONTEND_ORIGIN", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origins] if origins != "*" else ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok"}


def _latest_facts(db, company_id):
    rows = db.query(models.FinancialFact).filter_by(company_id=company_id).all()
    best = {}
    for r in rows:
        cur = best.get(r.concept)
        if cur is None or r.fiscal_year > cur[0]:
            best[r.concept] = (r.fiscal_year, r.value)
    return {k: v[1] for k, v in best.items()}


@app.get("/api/companies")
def list_companies(db: Session = Depends(get_db)):
    rows = []
    for co in db.query(models.Company).all():
        data = build_company(db, co)
        a = assumptions_dict(co.assumptions)
        try:
            rec = engines.recommend(data, a)
        except Exception:
            continue

        f = rec["fundamentals"]
        facts = _latest_facts(db, co.id)

        row = {
            # Identity
            "ticker":    co.ticker,
            "name":      co.name,
            "sector":    co.sector,
            "type":      co.type,

            # Real values from DB — used by frontend buildFromApi
            "shares":      co.shares_outstanding,           # actual shares (cr)
            "equity":      facts.get(K.NET_WORTH),          # ₹ cr
            "net_profit":  facts.get(K.NET_PROFIT),         # ₹ cr
            "revenue":     facts.get(K.REVENUE),            # ₹ cr (non-fin only)
            "net_debt":    facts.get(K.NET_DEBT),           # ₹ cr (non-fin only)

            # NBFC-specific (financial type only)
            "aum":         facts.get(K.AUM),
            "gnpa":        facts.get(K.GNPA),
            "nnpa":        facts.get(K.NNPA),
            "crar":        facts.get(K.CRAR),
            "nim":         facts.get(K.NIM),
            "roa":         facts.get(K.ROA),

            # Market
            "price":       data["price"],

            # Computed screener metrics
            "intrinsic":   rec["valuation"]["intrinsic"],
            "mos":         rec["mos"],
            "roe":         f["roe"],
            "pb":          f["pb"],
            "pe":          f["pe"],
            "composite":   rec["composite"],
            "verdict":     rec["verdict"],
        }
        rows.append(row)

    rows.sort(key=lambda r: r["composite"], reverse=True)
    return rows


def _get_or_404(db, ticker):
    co = db.query(models.Company).filter_by(ticker=ticker.upper()).first()
    if not co:
        raise HTTPException(404, f"Unknown ticker {ticker}")
    return co


@app.get("/api/companies/{ticker}")
def company_detail(ticker: str, db: Session = Depends(get_db)):
    co = _get_or_404(db, ticker)
    data = build_company(db, co)
    a = assumptions_dict(co.assumptions)
    rec = engines.recommend(data, a)
    sens = engines.sensitivity(data, a)
    return {"company": _public(data), "assumptions": a,
            "recommendation": rec, "sensitivity": sens}


@app.post("/api/companies/{ticker}/valuation")
def recompute(ticker: str, override: AssumptionOverride,
              db: Session = Depends(get_db)):
    co = _get_or_404(db, ticker)
    data = build_company(db, co)
    a = assumptions_dict(co.assumptions)
    payload = override.dict(exclude_none=True)
    if "price" in payload:
        data["price"] = payload.pop("price")
    a.update(payload)
    rec = engines.recommend(data, a)
    sens = engines.sensitivity(data, a)
    return {"company": _public(data), "assumptions": a,
            "recommendation": rec, "sensitivity": sens}


@app.post("/api/companies/{ticker}/onepager")
def company_onepager(ticker: str, db: Session = Depends(get_db)):
    """
    Generate a PDF one-pager for the company.
    Returns PDF binary with Content-Type: application/pdf.
    """
    from fastapi.responses import Response
    from collections import defaultdict

    co = _get_or_404(db, ticker)

    # Market data
    price   = co.market.price if co.market else 0
    market  = {
        "price":   price,
        "chgPct":  0,
        "mcapCr":  price * co.shares_outstanding,
        "pe":      None,
        "pb":      None,
    }

    # Financials
    hist_rows = (
        db.query(models.HistoricalFinancial)
        .filter_by(company_id=co.id)
        .order_by(models.HistoricalFinancial.fiscal_year)
        .all()
    )
    hist: dict = defaultdict(lambda: defaultdict(dict))
    for row in hist_rows:
        if row.value is not None:
            hist[row.fiscal_year][row.statement_type][row.line_item] = row.value
    hist_clean = {yr: {stmt: dict(items) for stmt, items in stmts.items()} for yr, stmts in hist.items()}
    from app.financials import build_financials_response
    financials = build_financials_response(co, hist_rows)

    # Metrics
    facts   = _latest_facts(db, co.id)
    template = co.template_code or "MANUFACTURING"
    metrics = {}
    try:
        from app.metrics import compute_metrics
        metrics = compute_metrics(co, facts, hist_clean, price, template)
    except Exception:
        pass

    # Thesis (cached)
    thesis = None
    try:
        from app.thesis import generate_thesis
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if api_key:
            t = generate_thesis(co, facts, hist_clean, metrics, price, [], api_key)
            thesis = t.get("thesis")
    except Exception:
        pass

    # Intrinsic (simple justified P/B proxy if no DCF)
    intrinsic = None
    try:
        eq = facts.get("NET_WORTH") or co.shares_outstanding * 200
        pat = facts.get("NET_PROFIT") or 0
        roe = pat / eq if eq else 0
        ke = 0.071 + 0.86 * 0.085
        g  = 0.05
        if ke > g:
            pb = roe / (ke - g)
            intrinsic = (eq / co.shares_outstanding) * pb
    except Exception:
        pass

    pdf_bytes = build_onepager(co, market, financials, metrics, intrinsic, thesis)

    safe_name = co.ticker.replace(" ", "_")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}_onepager.pdf"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )


def _public(data):
    return {k: v for k, v in data.items() if k != "series"}
