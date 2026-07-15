"""
FastAPI application — fixed to return real financial data from DB.

Key fix: /api/companies now returns shares_outstanding, equity, net_profit,
revenue, net_debt so the frontend can build accurate DCF models instead
of back-calculating from ratios.
"""
import os
import threading
import time
from collections import defaultdict, deque

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy.orm import Session

from .database import Base, engine, get_db
from . import models, engines
from .assemble import build_company, assumptions_dict, effective_assumptions
from .consensus import analyst_consensus
from .schemas import AssumptionOverride
from . import concepts as K

Base.metadata.create_all(bind=engine)

# create_all never ALTERs existing tables — additive columns need a nudge.
# Idempotent and dialect-tolerant: failures (column exists) are expected.
def _additive_migrations():
    from sqlalchemy import text
    stmts = [
        "ALTER TABLE portfolio_holdings ADD COLUMN buy_date DATE",
    ]
    with engine.connect() as conn:
        for stmt in stmts:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass

_additive_migrations()

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
from app.watchlist_routes import router as watchlist_router
app.include_router(watchlist_router)
from app.compare_routes import router as compare_router
app.include_router(compare_router)
from app.results_routes import router as results_router
app.include_router(results_router)
from app.ownership_routes import router as ownership_router
app.include_router(ownership_router)
from app.operations_routes import router as operations_router
app.include_router(operations_router)
from app.logo_routes import router as logo_router
app.include_router(logo_router)
from app.backtest_routes import router as backtest_router
app.include_router(backtest_router)
from app.export_routes import router as export_router
app.include_router(export_router)
from app.portfolio_routes import router as portfolio_router
app.include_router(portfolio_router)
from app.scenario_routes import router as scenario_router
app.include_router(scenario_router)
from app.dhan_routes import router as dhan_router
app.include_router(dhan_router)
from app.documents_routes import router as documents_router
app.include_router(documents_router)
from app.thesis_routes import router as thesis_router
app.include_router(thesis_router)
from app.auth_routes import router as auth_router
app.include_router(auth_router)
from app.quality_routes import router as quality_router
app.include_router(quality_router)
from app.screens_routes import router as screens_router
app.include_router(screens_router)
from app.admin_routes import router as admin_router
app.include_router(admin_router)

from app.ipo_routes import router as ipo_router
app.include_router(ipo_router)

from app.mf_routes import router as mf_router
app.include_router(mf_router)

from app.intraday_routes import router as intraday_router
app.include_router(intraday_router)

from app.macro_routes import router as macro_router
app.include_router(macro_router)

from app.onepager import build_onepager


def _debug_enabled() -> bool:
    """Tracebacks in API error payloads only when DEBUG=1/true."""
    return os.getenv("DEBUG", "").lower() in ("1", "true")


# ── Security hardening middleware ────────────────────────────────────────────
class RateLimitMiddleware(BaseHTTPMiddleware):
    """In-memory sliding 60s window per client IP.

    General routes: 240 req/min. Auth routes (/api/auth/...): 10 req/min so
    credential stuffing / signup spam is throttled hard. Client identity is the
    first X-Forwarded-For hop when present (we sit behind a proxy in prod),
    else the socket peer."""
    WINDOW = 60.0
    GENERAL_LIMIT = int(os.getenv("RATE_LIMIT_GENERAL", "240"))
    AUTH_LIMIT = int(os.getenv("RATE_LIMIT_AUTH", "10"))

    def __init__(self, app):
        super().__init__(app)
        self._lock = threading.Lock()
        self._general: dict[str, deque] = defaultdict(deque)
        self._auth: dict[str, deque] = defaultdict(deque)

    @staticmethod
    def _client_ip(request: Request) -> str:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            first = fwd.split(",")[0].strip()
            if first:
                return first
        return request.client.host if request.client else "unknown"

    def _allow(self, bucket: dict, ip: str, limit: int, now: float) -> bool:
        q = bucket[ip]
        cutoff = now - self.WINDOW
        while q and q[0] <= cutoff:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True

    async def dispatch(self, request: Request, call_next):
        ip = self._client_ip(request)
        now = time.time()
        is_auth = request.url.path.startswith("/api/auth")
        with self._lock:
            ok = self._allow(self._general, ip, self.GENERAL_LIMIT, now)
            if ok and is_auth:
                ok = self._allow(self._auth, ip, self.AUTH_LIMIT, now)
        if not ok:
            return JSONResponse({"error": "rate_limited"}, status_code=429)
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


app.add_middleware(RateLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

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


def _all_latest_facts(db):
    """Latest value per (company, concept) for EVERY company in ONE query —
    replaces the per-company _latest_facts call in the list endpoint (which was
    ~500 round-trips through the DB pooler and timed the screener out cold)."""
    best = {}  # (cid, concept) -> (year, value)
    for r in db.query(models.FinancialFact).all():
        k = (r.company_id, r.concept)
        cur = best.get(k)
        if cur is None or r.fiscal_year > cur[0]:
            best[k] = (r.fiscal_year, r.value)
    out = {}
    for (cid, concept), (yr, val) in best.items():
        out.setdefault(cid, {})[concept] = val
    return out


_COMPANIES_CACHE = {"ts": 0.0, "data": None}
_FACTORS_CACHE = {"ts": 0.0, "data": None}
_TECH_CACHE = {"ts": 0.0, "data": None}

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


@app.get("/api/universe")
def api_universe():
    """Tickers the terminal currently EXPOSES — the SINGLE source of truth for
    the frontend's visibility whitelist, so backend and frontend can no longer
    drift. The tier is selected by the UNIVERSE_TIER env var (nifty100 |
    nifty250 | nifty500); membership sets live in indianapi_ingester.py."""
    from app.ingest.indianapi_ingester import (VISIBLE_UNIVERSE, UNIVERSE_TIER,
                                               NIFTY_50, NIFTY_NEXT_50)
    return {
        "tier": UNIVERSE_TIER,
        "count": len(VISIBLE_UNIVERSE),
        "tickers": sorted(VISIBLE_UNIVERSE),
        "core": sorted(NIFTY_50 | {"FEDFINA"}),
        "next": sorted(NIFTY_NEXT_50),
    }


@app.get("/api/factors")
def api_factors(db: Session = Depends(get_db)):
    """Multi-factor Alpha Score ranking of the visible universe (Nifty 100).

    A transparent value / quality / momentum / low-vol / growth composite — turns
    the universe into a ranked idea list with a factor breakdown. A research aid,
    NOT investment advice. Cached 5 min (same as /api/companies)."""
    from app.factors import FACTOR_WEIGHTS, sector_strength
    from app.signals import ranked_visible
    if _FACTORS_CACHE["data"] is not None and (time.time() - _FACTORS_CACHE["ts"]) < 300:
        return _FACTORS_CACHE["data"]

    ranked = ranked_visible(db)
    payload = {"count": len(ranked), "weights": FACTOR_WEIGHTS,
               "note": "Transparent multi-factor ranking (value/quality/momentum/low-vol/growth/"
                       "catalyst). A research aid, not investment advice.",
               "sectors": sector_strength(ranked),
               "ideas": ranked}
    _FACTORS_CACHE["data"], _FACTORS_CACHE["ts"] = payload, time.time()
    return payload


@app.get("/api/screen/technical")
def api_screen_technical(db: Session = Depends(get_db)):
    """Technical read across the visible universe from the 5-yr OHLCV — DMA
    states, 50/200 golden/death cross, RSI-14, 52-week-range position, 12-1
    momentum, and volume vs its 50-day average. A screening aid, not advice.
    Cached 5 min."""
    import datetime as _dt
    from app.factors import technicals
    from app.ingest.indianapi_ingester import VISIBLE_UNIVERSE
    if _TECH_CACHE["data"] is not None and time.time() - _TECH_CACHE["ts"] < 300:
        return _TECH_CACHE["data"]

    cos = {c.id: c for c in db.query(models.Company).all()
           if (c.ticker or "").upper() in VISIBLE_UNIVERSE}
    cutoff = (_dt.date.today() - _dt.timedelta(days=420)).isoformat()
    series: dict = {}
    q = (db.query(models.HistoricalPrice.company_id, models.HistoricalPrice.close,
                  models.HistoricalPrice.volume)
           .filter(models.HistoricalPrice.company_id.in_(list(cos.keys())),
                   models.HistoricalPrice.date >= cutoff)
           .order_by(models.HistoricalPrice.date))
    for cid, close, vol in q.all():
        if close:
            s = series.setdefault(cid, ([], []))
            s[0].append(close)
            s[1].append(vol)
    out = []
    for cid, (closes, vols) in series.items():
        t = technicals(closes, [v for v in vols if v])
        if not t:
            continue
        c = cos[cid]
        out.append({"ticker": c.ticker, "name": c.name, "sector": c.sector, **t})
    out.sort(key=lambda r: (r.get("mom_12_1") if r.get("mom_12_1") is not None else -1e9),
             reverse=True)
    payload = {"count": len(out), "items": out,
               "note": "Technical read from the 5-yr OHLCV. A screening aid, not advice."}
    _TECH_CACHE["data"], _TECH_CACHE["ts"] = payload, time.time()
    return payload


@app.get("/api/factors/backtest")
def api_factors_backtest(db: Session = Depends(get_db)):
    """Public factor track record: forward return by Alpha-Score bucket, read
    from the AlphaSnapshot ledger (each name bucketed by its FIRST snapshot's
    Alpha, marked to today's price). Honest and forward — meaningful once
    snapshots span time; returns an empty-but-valid shape until then."""
    from app.factors import alpha_backtest
    try:
        snaps = (db.query(models.AlphaSnapshot)
                   .order_by(models.AlphaSnapshot.company_id, models.AlphaSnapshot.date).all())
    except Exception:
        db.rollback()
        return {"tracking_since": None, "snapshot_days": 0, "n": 0, "buckets": [], "top_minus_bottom": None}
    first = {}
    dates = set()
    for s in snaps:
        dates.add(s.date)
        if s.company_id not in first:      # earliest (rows are date-ascending per company)
            first[s.company_id] = s
    price_now = {m.company_id: m.price for m in db.query(models.MarketSnapshot).all()}
    rows = [{"ticker": s.ticker, "alpha0": s.alpha_score, "price0": s.price,
             "price_now": price_now.get(cid)} for cid, s in first.items()]
    bt = alpha_backtest(rows)
    return {"tracking_since": (min(dates) if dates else None),
            "snapshot_days": len(dates),
            "note": "Forward return by Alpha bucket (Q1 = highest Alpha). If the model has signal, "
                    "Q1 should beat Q5 over time. Accrues from the tracking-since date.",
            **bt}


@app.get("/api/companies")
def list_companies(nifty50: bool = False, db: Session = Depends(get_db)):
    """Screener rows. The headline intrinsic/MoS/verdict are the INDEPENDENT
    model's own view (DCF/RI from history-derived drivers). The analyst
    consensus is returned in a SEPARATE `analyst` block — never blended into the
    intrinsic — so the screener can show both columns honestly.

    ?nifty50=true returns ONLY the Nifty 50 (the universe we actively cover), so
    the whole response fits in a single payload."""
    from app.ingest.indianapi_ingester import UNIVERSE
    import time as _t

    def _scope(rows):
        return [r for r in rows if r.get("ticker") in UNIVERSE] if nifty50 else rows

    if _COMPANIES_CACHE["data"] is not None and (_t.time() - _COMPANIES_CACHE["ts"]) < 300:
        return _scope(_COMPANIES_CACHE["data"])

    rows = []
    insights_by_cid = {r.company_id: r.data for r in db.query(models.CompanyInsight).all() if r.data}
    # Prefer precomputed independent valuations (instant); fall back to live.
    # Resilient: if the `valuations` table doesn't exist yet (fresh deploy before
    # the first precompute), don't 500 — just compute live for every row.
    val_by_cid = {}
    try:
        val_by_cid = {v.company_id: v for v in db.query(models.Valuation).all()}
    except Exception:
        db.rollback()   # clear the aborted transaction so live queries still work
        val_by_cid = {}

    # Batch-load facts and prices ONCE (was ~1000 per-company round-trips that
    # timed the screener out on a cold cache).
    facts_by_cid = _all_latest_facts(db)
    price_by_cid = {m.company_id: m.price for m in db.query(models.MarketSnapshot).all()}

    companies = db.query(models.Company).join(models.MarketSnapshot).all()
    for co in companies:
        price = price_by_cid.get(co.id)
        facts = facts_by_cid.get(co.id, {})

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
        try:
            cons = analyst_consensus(insights_by_cid.get(co.id), price)
        except Exception:
            cons = None

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
    _COMPANIES_CACHE["ts"], _COMPANIES_CACHE["data"] = _t.time(), rows
    return _scope(rows)


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
    from fastapi.responses import JSONResponse
    import traceback
    co = _get_or_404(db, ticker)
    try:
        data = build_company(db, co)
        # Same INDEPENDENT assumptions the screener uses → company page and screener
        # now agree exactly. Analyst consensus is returned separately, never blended.
        a = effective_assumptions(db, co, data)
        rec = engines.recommend(data, a)
        sens = engines.sensitivity(data, a)
        try:
            analyst = _consensus_block(db, co, data.get("price"))
        except Exception:
            analyst = None
        return {"company": _public(data), "assumptions": a,
                "recommendation": rec, "sensitivity": sens, "analyst": analyst}
    except Exception as e:
        body = {"error": str(e)}
        if _debug_enabled():
            body["trace"] = traceback.format_exc()[-1200:]
        return JSONResponse(body, status_code=500)


@app.post("/api/companies/{ticker}/valuation")
def recompute(ticker: str, override: AssumptionOverride,
              db: Session = Depends(get_db)):
    co = _get_or_404(db, ticker)
    data = build_company(db, co)
    # Start from the independent derived assumptions, then apply the user's
    # what-if overrides on top.
    a = effective_assumptions(db, co, data)
    payload = override.dict(exclude_none=True)
    if "price" in payload:
        data["price"] = payload.pop("price")
    a.update(payload)
    # Keep the internal keys (_valuation_sector, _drivers) for the engine —
    # stripping them here previously reverted every stock to MANUFACTURING
    # sector params on any what-if, jumping the fair value and flipping
    # verdicts. Hide them only from the JSON response.
    rec = engines.recommend(data, a)
    sens = engines.sensitivity(data, a)
    a_public = {k: v for k, v in a.items() if not k.startswith("_")}
    return {"company": _public(data), "assumptions": a_public,
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
        except Exception: pass
        # shares can legitimately be None/0 for thin seeds — never crash the PDF
        sh = co.shares_outstanding or 0
        market = {"price": price, "chgPct": 0,
                  "mcapCr": (price * sh) if (price and sh) else None}
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
            # pass the REAL statements (not {}), else every statement-derived
            # ratio — PAT CAGR, ROCE, growth — silently drops to "—" on the PDF.
            metrics = compute_metrics(co, facts, financials.get("statements") or {},
                                      price, template)
        except Exception: pass
        # Use the SAME independent engine as the rest of the terminal (no more
        # divorced inline DCF).
        intrinsic, rec, a = None, None, {}
        try:
            data = build_company(db, co)
            a = effective_assumptions(db, co, data)
            rec = engines.recommend(data, a)
            intrinsic = rec.get("intrinsic")
        except Exception:
            pass
        # per-stock scorecard (real green/red flags for the thesis)
        scorecard = None
        try:
            from app.manager_engine import load_evidence
            from app.scorecard import build_scorecard
            ev = ((load_evidence(db) or {}).get("names") or {}).get(co.ticker.upper())
            if ev:
                scorecard = build_scorecard(ev)
        except Exception:
            pass
        # analyst consensus (target / rating / upside) for the street-view block
        analyst, description = None, None
        try:
            from app.consensus import analyst_consensus
            ins = db.query(models.CompanyInsight).filter_by(company_id=co.id).first()
            analyst = analyst_consensus(ins.data if ins else None, price)
            if ins and ins.data:
                description = (((ins.data.get("profile_snapshot") or {}).get("payload") or {})
                              .get("description"))
        except Exception:
            pass
        # Latest quarter + historical CAGR (from the insight blob) and the concall
        # key points — the "what management just said / delivered" layer.
        quarter, cagr, concall = None, None, None
        try:
            if ins and ins.data:
                quarter = ins.data.get("results")
                cagr = ins.data.get("growth")
        except Exception:
            pass
        try:
            from app import transcript_ingester
            concall = transcript_ingester.load(db, co.ticker)
        except Exception:
            pass
        # a short peer set for the comparison table — same valuation sector AND a
        # comparable size (0.2x–5x market cap), so we never pit a small name
        # against a mega-cap in a different business. Hidden unless ≥2 clean peers.
        peers = []
        try:
            vsec = (rec or {}).get("valuation_sector")
            mc = price * (co.shares_outstanding or 0)
            if vsec and mc:
                # explicit price map — the lazy .market relationship came back
                # empty in this query, zeroing every peer's market cap.
                price_by = {m.company_id: m.price
                            for m in db.query(models.MarketSnapshot).all()}
                q = (db.query(models.Valuation, models.Company)
                       .join(models.Company, models.Valuation.company_id == models.Company.id)
                       .filter(models.Valuation.valuation_sector == vsec,
                               models.Company.ticker != co.ticker))
                scored = []
                for val, pco in q.all():
                    ppx = price_by.get(pco.id) or 0
                    pmc = ppx * (pco.shares_outstanding or 0)
                    if not pmc or not (0.2 * mc <= pmc <= 5 * mc):
                        continue
                    scored.append((abs(pmc - mc), val, pco))
                for _, val, pco in sorted(scored, key=lambda x: x[0])[:5]:
                    peers.append({"ticker": pco.ticker, "pe": val.pe, "pb": val.pb,
                                  "roe": val.roe, "mos": val.mos, "verdict": val.verdict})
            if len(peers) < 2:      # a lone/odd "peer" is worse than none
                peers = []
        except Exception:
            pass
        pdf_bytes = build_onepager(co, market, financials, metrics, intrinsic,
                                   None, rec=rec, scorecard=scorecard,
                                   assumptions=a, analyst=analyst, peers=peers,
                                   description=description, quarter=quarter,
                                   cagr=cagr, concall=concall)
        return Response(content=pdf_bytes, media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{co.ticker}_onepager.pdf"',
                                 "Content-Length": str(len(pdf_bytes))})
    except Exception as e:
        body = {"error": str(e)}
        if _debug_enabled():
            body["trace"] = traceback.format_exc()[-800:]
        return JSONResponse(body, status_code=500)


def _public(data):
    return {k: v for k, v in data.items() if k != "series"}


@app.get("/api/isin-map")
def isin_map():
    """ISIN → ticker for the whole universe (from stored profile snapshots).
    Broker exports (Groww/Zerodha console) identify rows by ISIN + truncated
    display names — ISIN is the only unambiguous key. Cached in-process 1h."""
    import time as _time
    from app.database import SessionLocal
    global _ISIN_CACHE
    try:
        ts, data = _ISIN_CACHE
    except NameError:
        ts, data = 0, None
    if data is not None and _time.time() - ts < 3600:
        return data
    import re as _re
    def _norm(n):
        n = _re.sub(r"\b(ltd|limited|company|corp|corporation|industries|enterprises|the|and|of|india)\b\.?",
                    " ", (n or "").lower().replace("&", " and "))
        return " ".join(_re.split(r"[^a-z0-9]+", n)).strip()
    s = SessionLocal()
    try:
        out, names = {}, {}
        for co in s.query(models.Company).all():
            nm = _norm(co.name)
            if nm:
                names[nm] = co.ticker
        rows = (s.query(models.Company, models.CompanyInsight)
                  .join(models.CompanyInsight,
                        models.CompanyInsight.company_id == models.Company.id).all())
        for co, ins in rows:
            d = ins.data or {}
            isin = (((d.get("profile_snapshot") or {}).get("payload") or {})
                    .get("key_facts") or {}).get("isin")
            if isin:
                out[str(isin).strip().upper()] = co.ticker
    finally:
        s.close()
    payload = {"count": len(out), "map": out, "names": names}
    _ISIN_CACHE = (_time.time(), payload)
    return payload
