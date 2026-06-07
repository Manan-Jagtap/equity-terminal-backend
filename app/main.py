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

# India sector P/E medians — mirrors the frontend SECTOR_PE so the screener's
# consensus-anchored fair value matches the company DCF/Verdict pages.
_SCREENER_SECTOR_PE = {
    "IT_SERVICES": 25, "MANUFACTURING": 20, "CONSUMER": 36, "PHARMA": 30,
    "ENERGY": 12, "AUTO": 24, "METAL": 10, "TELECOM": 24, "CEMENT": 26,
    "UTILITIES": 15, "NBFC": 18, "BANK": 14, "INSURANCE": 26,
}


def _sector_template(sector):
    s = (sector or "").lower()
    for kw, tmpl in (
        ("information technology", "IT_SERVICES"), ("software", "IT_SERVICES"), ("technology", "IT_SERVICES"),
        ("bank", "BANK"), ("insurance", "INSURANCE"),
        ("fast moving", "CONSUMER"), ("fmcg", "CONSUMER"), ("consumer", "CONSUMER"), ("retail", "CONSUMER"),
        ("pharma", "PHARMA"), ("health", "PHARMA"),
        ("auto", "AUTO"), ("power", "UTILITIES"), ("utilit", "UTILITIES"),
        ("oil", "ENERGY"), ("gas", "ENERGY"), ("petroleum", "ENERGY"), ("energy", "ENERGY"),
        ("telecom", "TELECOM"), ("communication", "TELECOM"),
        ("metal", "METAL"), ("mining", "METAL"),
        ("construction material", "CEMENT"), ("cement", "CEMENT"),
        ("financ", "NBFC"),
    ):
        if kw in s:
            return tmpl
    return "MANUFACTURING"


def _consensus_overlay(price, backend_iv, sector, template_code, idata):
    """Blend the bottom-up intrinsic with the analyst consensus target + a
    forward-P/E value, then clamp into the analyst target range — identical to
    the frontend blendedValuation overlay. Returns (fv, target, rating) or
    (None, None, None) when there's no analyst target to anchor to."""
    if not idata or not price or price <= 0:
        return None, None, None

    def _f(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    tgt = idata.get("target") or {}
    target = _f(tgt.get("mean"))
    if not target or target <= 0:
        return None, None, None
    low, high = _f(tgt.get("low")), _f(tgt.get("high"))
    tmpl = template_code if template_code in _SCREENER_SECTOR_PE else _sector_template(sector)
    sector_pe = _SCREENER_SECTOR_PE.get(tmpl, 18)

    fwd_pe_val = None
    try:
        from app.history_routes import _eps_estimates
        ests = _eps_estimates(idata.get("forecasts")) or []
        if ests:
            fe = _f(ests[0].get("mean"))
            if fe and fe > 0:
                fwd_pe_val = fe * sector_pe
    except Exception:
        pass

    # Clamp each fundamental component into the analyst target band so a wrong
    # sector multiple / over-conservative DCF can't drag fair value below where
    # the analyst set actually sits.
    def _clampc(v):
        if v is None or v <= 0:
            return None
        if low and low > 0 and high and high > 0:
            return max(low, min(v, high))
        return v

    parts = [(target, 0.60)]
    fpv = _clampc(fwd_pe_val)
    if fpv:
        parts.append((fpv, 0.20))
    biv = _clampc(backend_iv)
    if biv:
        parts.append((biv, 0.20))
    wsum = sum(w for _, w in parts)
    fv = sum(v * w for v, w in parts) / wsum
    if low and low > 0 and fv < low * 0.95:
        fv = low * 0.95
    if high and high > 0 and fv > high * 1.05:
        fv = high * 1.05
    rating = (idata.get("analyst") or {}).get("rating")
    return fv, target, rating


def _screener_verdict(exp_ret, rating):
    if exp_ret is None:
        return None
    bull = bool(rating) and ("buy" in str(rating).lower())
    if exp_ret >= 0.15 and bull:
        return "BUY"
    if exp_ret >= 0.06:
        return "ACCUMULATE"
    if exp_ret >= -0.07:
        return "HOLD"
    if exp_ret >= -0.20:
        return "REDUCE"
    return "AVOID"


_VERDICT_RANK = {"BUY": 5, "ACCUMULATE": 4, "HOLD": 3, "REDUCE": 2, "AVOID": 1}


@app.get("/api/companies")
def list_companies(db: Session = Depends(get_db)):
    import time as _t
    if _COMPANIES_CACHE["data"] is not None and (_t.time() - _COMPANIES_CACHE["ts"]) < 300:
        return _COMPANIES_CACHE["data"]

    rows = []
    # Analyst insights (target + forecasts + rating) keyed by company — loaded
    # once so the consensus overlay below doesn't query per row.
    insights_by_cid = {}
    for r in db.query(models.CompanyInsight).all():
        if r.data:
            insights_by_cid[r.company_id] = r.data

    # Only companies that actually have ingested market data — skips the ~450
    # un-ingested seed names, so the loop stays small and fast (and can't time out).
    companies = db.query(models.Company).join(models.MarketSnapshot).all()
    for co in companies:
        try:
            data = build_company(db, co)
            a = assumptions_dict(co.assumptions)
            rec = engines.recommend(data, a)
        except Exception:
            # One bad company must never take down the whole screener.
            continue

        f = rec["fundamentals"]
        facts = _latest_facts(db, co.id)

        # Consensus overlay → intrinsic / MoS / verdict that match the company
        # pages (analyst target + forward P/E + bottom-up DCF, clamped to range).
        intrinsic, mos, verdict = rec.get("intrinsic"), rec["mos"], rec["verdict"]
        price = data["price"]
        fv, target, rating = _consensus_overlay(
            price, rec.get("intrinsic"), co.sector, co.template_code, insights_by_cid.get(co.id))
        if fv and price:
            intrinsic = round(fv, 2)
            mos = (fv - price) / price
            analyst_up = (target - price) / price if target else mos
            exp_ret = (mos + analyst_up) / 2
            v = _screener_verdict(exp_ret, rating)
            if v:
                verdict = v

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

            # Consensus-anchored screener metrics (match the company pages)
            "intrinsic":   intrinsic,
            "mos":         mos,
            "roe":         f["roe"],
            "pb":          f["pb"],
            "pe":          f["pe"],
            "composite":   rec["composite"],
            "verdict":     verdict,
            "confidence":  rec["confidence"]["level"],
            "confidence_score": rec["confidence"]["score"],
            "reliable":    rec["reliable"],
        }
        rows.append(row)

    # Rank: reliable names first, then by verdict (BUY → AVOID), then by upside,
    # so the top of the screener surfaces the most attractive consensus-aligned
    # opportunities instead of an undifferentiated wall of AVOID.
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
        intrinsic = None
        try:
            eq = facts.get("NET_WORTH") or co.shares_outstanding * 200
            pat = facts.get("NET_PROFIT") or 0
            ke, g = 0.071 + 0.86 * 0.085, 0.05
            if eq and pat and ke > g:
                intrinsic = (eq / co.shares_outstanding) * ((pat/eq) / (ke - g))
        except: pass
        pdf_bytes = build_onepager(co, market, financials, metrics, intrinsic, None)
        return Response(content=pdf_bytes, media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{co.ticker}_onepager.pdf"',
                                 "Content-Length": str(len(pdf_bytes))})
    except Exception as e:
        return JSONResponse({"error": str(e), "trace": traceback.format_exc()[-800:]}, status_code=500)


def _public(data):
    return {k: v for k, v in data.items() if k != "series"}
