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
from app.bse_routes import router as bse_router
app.include_router(bse_router)
from app.market_routes import router as market_router
app.include_router(market_router)
from app.profile_routes import router as profile_router
app.include_router(profile_router)
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


_COMPANIES_CACHE = {"ts": 0.0, "data": None}

_VERDICT_RANK = {"BUY": 5, "ACCUMULATE": 4, "HOLD": 3, "REDUCE": 2, "AVOID": 1}

# Verdicts the old engine could emit; map to the current scheme just in case a
# stale precomputed Valuation row is read.
_NORMALIZE_VERDICT = {"TRIM": "REDUCE"}


def _live_recommend(db, co):
    """Run the INDEPENDENT model live for one company (used when no precomputed
    Valuation row exists). Returns the model dict or None on failure."""
    try:
        data = build_company(db, co)
        a = effective_assumptions(db, co, data)
        rec = engines.recommend(data, a)
    except Exception:
        return None
    f = rec["fundamentals"]
    return {
        "intrinsic": rec.get("intrinsic"), "mos": rec.get("mos"),
        "verdict": rec.get("verdict"), "composite": rec.get("composite"),
        "reliable": bool(rec.get("reliable")),
        "confidence": rec["confidence"]["level"],
        "roe": f["roe"], "pb": f["pb"], "pe": f["pe"],
        "valuation_sector": rec.get("valuation_sector"),
    }


@app.get("/api/companies")
def list_companies(db: Session = Depends(get_db)):
    """Screener rows. The headline intrinsic/MoS/verdict are the INDEPENDENT
    model's own view (DCF/RI from history-derived drivers). The analyst
    consensus is returned in a SEPARATE `analyst` block — never blended into the
    intrinsic — so the screener can show both columns honestly."""
    import time as _t
    if _COMPANIES_CACHE["data"] is not None and (_t.time() - _COMPANIES_CACHE["ts"]) < 300:
        return _COMPANIES_CACHE["data"]

    rows = []
    insights_by_cid = {r.company_id: r.data for r in db.query(models.CompanyInsight).all() if r.data}
    # Prefer precomputed independent valuations (instant); fall back to live.
    val_by_cid = {v.company_id: v for v in db.query(models.Valuation).all()}

    companies = db.query(models.Company).join(models.MarketSnapshot).all()
    for co in companies:
        price = co.market.price if co.market else None
        facts = _latest_facts(db, co.id)

        v = val_by_cid.get(co.id)
        if v is not None and v.intrinsic is not None:
            m = {"intrinsic": v.intrinsic, "mos": v.mos, "verdict": v.verdict,
                 "composite": v.composite, "reliable": bool(v.reliable),
                 "confidence": v.confidence, "roe": v.roe, "pb": v.pb, "pe": v.pe,
                 "valuation_sector": v.valuation_sector}
        else:
            m = _live_recommend(db, co)
            if m is None:
                continue

        verdict = _NORMALIZE_VERDICT.get(m["verdict"], m["verdict"])
        cons = analyst_consensus(insights_by_cid.get(co.id), price)

        rows.append({
            "ticker": co.ticker, "name": co.name, "sector": co.sector, "type": co.type,
            "shares": co.shares_outstanding,
            "equity": facts.get(K.NET_WORTH), "net_profit": facts.get(K.NET_PROFIT),
            "revenue": facts.get(K.REVENUE), "net_debt": facts.get(K.NET_DEBT),
            "aum": facts.get(K.AUM), "gnpa": facts.get(K.GNPA), "nnpa": facts.get(K.NNPA),
            "crar": facts.get(K.CRAR), "nim": facts.get(K.NIM), "roa": facts.get(K.ROA),
            "price": price,
            # INDEPENDENT model (headline)
            "intrinsic": m["intrinsic"], "mos": m["mos"], "verdict": verdict,
            "composite": m["composite"], "reliable": m["reliable"],
            "confidence": m["confidence"], "valuation_sector": m.get("valuation_sector"),
            "roe": m["roe"], "pb": m["pb"], "pe": m["pe"],
            # ANALYST consensus (separate; for the consensus column/tab)
            "analyst": cons,
            "analyst_target": (cons or {}).get("target"),
            "analyst_upside": (cons or {}).get("upside"),
            "analyst_rating": (cons or {}).get("rating"),
        })

    # Rank: reliable first, then independent verdict (BUY→AVOID), then upside.
    rows.sort(key=lambda r: (
        r["reliable"],
        _VERDICT_RANK.get(r["verdict"], 0),
        r["mos"] if r.get("mos") is not None else -9,
    ), reverse=True)
    import time as _t
    _COMPANIES_CACHE["ts"], _COMPANIES_CACHE["data"] = _t.time(), rows
    return rows


_PEER_UNIV_CACHE = {"ts": 0.0, "data": None}


@app.get("/api/peer_universe")
def peer_universe(db: Session = Depends(get_db)):
    """Every company's market multiples (P/E, P/B, ROE TTM, net margin, div
    yield, price, rating) on ONE consistent IndianAPI basis, with its sector —
    so the Peer Universe tab can compare across the whole sector and compute a
    median, not just the 5–6 peers IndianAPI returns per company."""
    import time as _t
    if _PEER_UNIV_CACHE["data"] is not None and (_t.time() - _PEER_UNIV_CACHE["ts"]) < 1800:
        return _PEER_UNIV_CACHE["data"]
    out = []
    try:
        from app.history_routes import _peer_metrics_map
        pm = _peer_metrics_map(db) or {}
        # company_id → its own IndianAPI ticker_id (to look up self-metrics)
        tid_by_cid = {}
        for r in db.query(models.CompanyInsight).all():
            if getattr(r, "ticker_id", None):
                tid_by_cid[r.company_id] = r.ticker_id

        def _r(x, n=2):
            try:
                return round(float(x), n)
            except (TypeError, ValueError):
                return None

        # Build from EVERY ingested company (has a MarketSnapshot). Multiples are
        # computed from the DB; we OVERLAY IndianAPI self-metrics when present so
        # the numbers match the rest of the terminal where coverage exists.
        for co in db.query(models.Company).join(models.MarketSnapshot).all():
            try:
                price = co.market.price if co.market else None
                facts = _latest_facts(db, co.id)
                nw, npf, rev = facts.get(K.NET_WORTH), facts.get(K.NET_PROFIT), facts.get(K.REVENUE)
                sh = co.shares_outstanding
                eps  = (npf / sh) if (npf and sh) else None
                bvps = (nw / sh) if (nw and sh) else None
                pe  = (price / eps) if (price and eps and eps > 0) else None
                pb  = (price / bvps) if (price and bvps and bvps > 0) else None
                roe = (npf / nw * 100) if (npf and nw and nw > 0) else None
                npm = (npf / rev * 100) if (npf and rev and rev > 0) else None
                m = pm.get(tid_by_cid.get(co.id)) or {}
                pick = lambda k, fb: (m.get(k) if m.get(k) is not None else fb)
                out.append({
                    "ticker": co.ticker, "name": co.name, "sector": co.sector,
                    "price":    pick("price", _r(price)),
                    "pe":       pick("pe", _r(pe)),
                    "pb":       pick("pb", _r(pb)),
                    "roe_ttm":  pick("roe_ttm", _r(roe, 1)),
                    "npm_ttm":  pick("npm_ttm", _r(npm, 1)),
                    "div_yield": m.get("div_yield"),
                    "rating":   m.get("rating"),
                })
            except Exception:
                continue
        out.sort(key=lambda x: x.get("name") or "")
    except Exception:
        return _PEER_UNIV_CACHE["data"] or []
    _PEER_UNIV_CACHE["ts"], _PEER_UNIV_CACHE["data"] = _t.time(), out
    return out


def _get_or_404(db, ticker):
    co = db.query(models.Company).filter_by(ticker=ticker.upper()).first()
    if not co:
        raise HTTPException(404, f"Unknown ticker {ticker}")
    return co


def _consensus_block(db, co, price):
    ins = db.query(models.CompanyInsight).filter_by(company_id=co.id).first()
    return analyst_consensus(ins.data if ins else None, price)


@app.get("/api/companies/{ticker}")
def company_detail(ticker: str, db: Session = Depends(get_db)):
    co = _get_or_404(db, ticker)
    data = build_company(db, co)
    # Same INDEPENDENT assumptions the screener uses → company page and screener
    # now agree exactly. Analyst consensus is returned separately, never blended.
    a = effective_assumptions(db, co, data)
    rec = engines.recommend(data, a)
    sens = engines.sensitivity(data, a)
    return {"company": _public(data), "assumptions": a,
            "recommendation": rec, "sensitivity": sens,
            "analyst": _consensus_block(db, co, data.get("price"))}


@app.post("/api/companies/{ticker}/valuation")
def recompute(ticker: str, override: AssumptionOverride,
              db: Session = Depends(get_db)):
    co = _get_or_404(db, ticker)
    data = build_company(db, co)
    # Start from the independent derived assumptions, then apply the user's
    # what-if overrides on top.
    a = effective_assumptions(db, co, data)
    a = {k: v for k, v in a.items() if not k.startswith("_")}
    payload = override.dict(exclude_none=True)
    if "price" in payload:
        data["price"] = payload.pop("price")
    a.update(payload)
    rec = engines.recommend(data, a)
    sens = engines.sensitivity(data, a)
    return {"company": _public(data), "assumptions": a,
            "recommendation": rec, "sensitivity": sens,
            "analyst": _consensus_block(db, co, data.get("price"))}


@app.post("/api/companies/{ticker}/onepager")
def company_onepager(ticker: str, db: Session = Depends(get_db)):
    from fastapi.responses import Response, JSONResponse
    from collections import defaultdict
    import traceback
    try:
        co = _get_or_404(db, ticker)
        price = 0
        try: price = co.market.price or 0
        except: pass
        market = {"price": price, "chgPct": 0, "mcapCr": price * co.shares_outstanding}
        hist_rows = (db.query(models.HistoricalFinancial)
                     .filter_by(company_id=co.id)
                     .order_by(models.HistoricalFinancial.fiscal_year).all())
        from app.financials import build_financials_response
        financials = build_financials_response(co, hist_rows)
        facts = _latest_facts(db, co.id)
        template = co.template_code or "MANUFACTURING"
        metrics = {}
        try:
            from app.metrics import compute_metrics
            metrics = compute_metrics(co, facts, {}, price, template)
        except: pass
        # Use the SAME independent engine as the rest of the terminal (no more
        # divorced inline DCF).
        intrinsic = None
        try:
            data = build_company(db, co)
            a = effective_assumptions(db, co, data)
            intrinsic = engines.recommend(data, a).get("intrinsic")
        except Exception:
            pass
        pdf_bytes = build_onepager(co, market, financials, metrics, intrinsic, None)
        return Response(content=pdf_bytes, media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{co.ticker}_onepager.pdf"',
                                 "Content-Length": str(len(pdf_bytes))})
    except Exception as e:
        return JSONResponse({"error": str(e), "trace": traceback.format_exc()[-800:]}, status_code=500)


def _public(data):
    return {k: v for k, v in data.items() if k != "series"}
