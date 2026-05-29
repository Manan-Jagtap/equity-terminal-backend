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
from .financials import build_financials_response
from .templates import classify, TemplateCode
from .metrics import compute_metrics, METRIC_BY_KEY
from .thesis import generate_thesis

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Equity Research Terminal API", version="2.0")

from app.history_routes import router as history_router
app.include_router(history_router)

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


@app.get("/api/companies/{ticker}/metrics")
def company_metrics(
    ticker: str,
    category: str | None = None,
    db: Session = Depends(get_db),
):
    """
    Returns 80+ computed metrics for the given ticker, grouped by category.
    Categories vary by template_code (e.g. NBFC gets GNPA/NIM/CRAR block).

    Optional query param: ?category=Profitability
    Available categories: Growth, Profitability, Returns, Liquidity,
      Leverage, Efficiency, Cash Flow, Per Share, Valuation,
      Capital Structure, NBFC / Banking, Market
    """
    co = _get_or_404(db, ticker)

    # Build facts dict (latest values)
    facts = _latest_facts(db, co.id)

    # Build historical statements dict
    hist_rows = (
        db.query(models.HistoricalFinancial)
        .filter_by(company_id=co.id)
        .order_by(models.HistoricalFinancial.fiscal_year)
        .all()
    )
    from collections import defaultdict
    hist: dict = defaultdict(lambda: defaultdict(dict))
    for row in hist_rows:
        if row.value is not None:
            hist[row.fiscal_year][row.statement_type][row.line_item] = row.value
    hist = {yr: {stmt: dict(items) for stmt, items in stmts.items()}
            for yr, stmts in hist.items()}

    price = co.market.price if co.market else 0.0
    template = co.template_code or classify(co.sector)

    return compute_metrics(co, facts, hist, price, template, category)


@app.post("/api/companies/{ticker}/thesis")
def company_thesis(
    ticker: str,
    force_refresh: bool = False,
    db: Session = Depends(get_db),
):
    """
    Generate an AI investment thesis for the given ticker.

    Requires ANTHROPIC_API_KEY set as a Railway environment variable.
    Results are cached for 6 hours per (ticker, data_hash) pair.

    Optional query param: ?force_refresh=true to bypass cache.
    """
    co = _get_or_404(db, ticker)

    # Gather all data
    facts = _latest_facts(db, co.id)

    hist_rows = (
        db.query(models.HistoricalFinancial)
        .filter_by(company_id=co.id)
        .order_by(models.HistoricalFinancial.fiscal_year)
        .all()
    )
    from collections import defaultdict
    hist: dict = defaultdict(lambda: defaultdict(dict))
    for row in hist_rows:
        if row.value is not None:
            hist[row.fiscal_year][row.statement_type][row.line_item] = row.value
    hist = {yr: {stmt: dict(items) for stmt, items in stmts.items()}
            for yr, stmts in hist.items()}

    price = co.market.price if co.market else 0.0
    template = co.template_code or classify(co.sector)

    metrics_result = compute_metrics(co, facts, hist, price, template)

    # Get screener peers (same type, top by composite)
    peer_rows = []
    try:
        all_cos = db.query(models.Company).filter(
            models.Company.type == co.type,
            models.Company.ticker != co.ticker,
        ).limit(20).all()
        for peer in all_cos[:6]:
            peer_facts = _latest_facts(db, peer.id)
            peer_price = peer.market.price if peer.market else 0.0
            peer_eq = peer_facts.get("NET_WORTH") or 1
            peer_pat = peer_facts.get("NET_PROFIT")
            peer_roe = peer_pat / peer_eq if peer_pat and peer_eq else None
            peer_bvps = peer_eq / peer.shares_outstanding if peer.shares_outstanding else 1
            peer_eps = peer_pat / peer.shares_outstanding if peer_pat else None
            peer_rows.append({
                "ticker":  peer.ticker,
                "name":    peer.name,
                "roe":     peer_roe,
                "pe":      peer_price / peer_eps if peer_eps and peer_eps > 0 else None,
                "pb":      peer_price / peer_bvps if peer_bvps > 0 else None,
                "mos":     None,
                "verdict": "—",
            })
    except Exception:
        pass

    api_key = os.getenv("ANTHROPIC_API_KEY", "")

    return generate_thesis(
        company=co,
        facts=facts,
        hist_statements=hist,
        metrics_result=metrics_result,
        price=price,
        peer_rows=peer_rows,
        api_key=api_key,
        force_refresh=force_refresh,
    )


def _public(data):
    return {k: v for k, v in data.items() if k != "series"}


@app.get("/api/companies/{ticker}/financials")
def company_financials(ticker: str, db: Session = Depends(get_db)):
    """Sector-aware financial statements (P&L shape depends on template_code)."""
    co = _get_or_404(db, ticker)
    hist_fins = (
        db.query(models.HistoricalFinancial)
        .filter_by(company_id=co.id)
        .order_by(models.HistoricalFinancial.fiscal_year)
        .all()
    )
    return build_financials_response(co, hist_fins)


@app.get("/api/companies/{ticker}/template")
def company_template(ticker: str, db: Session = Depends(get_db)):
    """Template metadata for a ticker — useful for debugging."""
    co = _get_or_404(db, ticker)
    template = co.template_code or classify(co.sector)
    from .templates import is_financial
    return {
        "ticker":       co.ticker,
        "template":     template,
        "is_financial": is_financial(template),
        "sector":       co.sector,
        "description":  {
            TemplateCode.NBFC:          "NBFC — NII-based P&L, AUM, GNPA, NIM, CRAR",
            TemplateCode.BANK:          "Bank — Interest income, CASA, NIM, CRAR",
            TemplateCode.INSURANCE:     "Insurance — Premiums, Claims, Combined Ratio",
            TemplateCode.IT_SERVICES:   "IT Services — Revenue, EBIT, headcount metrics",
            TemplateCode.MANUFACTURING: "Manufacturing — Revenue, EBITDA, EBIT, CapEx",
            TemplateCode.CONSUMER:      "Consumer — Revenue, Gross Margin, EBITDA",
            TemplateCode.PHARMA:        "Pharma — Revenue, R&D, EBITDA",
            TemplateCode.ENERGY:        "Energy — Revenue, EBITDA, CapEx, EV/EBITDA",
        }.get(template, "Unknown template"),
    }
