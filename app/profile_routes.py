"""
profile_routes.py — generalized company Business-tab data for ALL companies,
served live from IndianAPI (6-hour cache, no re-ingestion needed).

GET /api/companies/{ticker}/profile  →  {
    description, industry, leadership[], shareholding[], key_facts{},
    corporate_actions[], concalls[], annual_reports[], credit_ratings[],
    announcements[]
}

All real IndianAPI data — no editorial / AI-generated content.
"""
import os, time, requests
from concurrent.futures import ThreadPoolExecutor
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app import models

router = APIRouter(prefix="/api/companies")

BASE = os.getenv("INDIANAPI_BASE", "https://stock.indianapi.in").rstrip("/")
KEY = os.getenv("INDIANAPI_KEY", "").strip()
TTL = 6 * 3600  # profiles change slowly

_cache: dict[str, tuple[float, object]] = {}
_out_calls = 0          # actual vendor HTTP hits since process start (budget)


def _get(path, params, ttl=TTL, empty_ok=True):
    global _out_calls
    ck = path + str(params)
    now = time.time()
    hit = _cache.get(ck)
    if hit and now - hit[0] < ttl:
        return hit[1]
    if not KEY:
        return None
    try:
        _out_calls += 1
        from app import vendor_meter; vendor_meter.tick()  # FIX-07
        r = requests.get(BASE + path, headers={"X-API-Key": KEY, "x-api-key": KEY},
                         params=params, timeout=25)
        data = r.json() if r.status_code == 200 else None
    except Exception:
        data = None
    # OUTCOME, not just spend (see vendor_meter.record / market_routes._get).
    # `empty_ok` defaults True: the document lists this helper serves
    # (/concalls, /annual_reports, /credit_ratings, /recent_announcements) are
    # legitimately empty for a small name with nothing filed, and
    # /historical_stats has answered every name with 200 {"info": ...} since
    # the vendor took it off-plan (DATA-12) — the Financials tabs still call it
    # per visit, so it must read as "upstream answered", which payload_ok does.
    # /stock is the exception: it never legitimately answers a ticker we track
    # with an empty body, and its call sites say so. An error envelope with a
    # 200 is a failure and is no longer cached over last-good (#140's fix).
    try:
        from app import vendor_meter as _vm
        ok = _vm.payload_ok(data, empty_ok=empty_ok)
        _vm.record(ok)
    except Exception:
        ok = data is not None
    if ok:
        _cache[ck] = (now, data)
        return data
    return hit[1] if hit else None


def _num(x):
    try:
        return float(str(x).replace(",", "").replace("%", "").strip())
    except (ValueError, AttributeError, TypeError):
        return None


def _as_list(x):
    return x if isinstance(x, list) else []


def _leadership(stock):
    prof = (stock or {}).get("companyProfile") or {}
    officers = (((prof.get("officers") or {}).get("officer")) or [])
    out = []
    for o in officers[:6]:
        if not isinstance(o, dict):
            continue
        name = " ".join(p for p in [o.get("firstName"), o.get("mI"), o.get("lastName")] if p)
        title = (o.get("title") or {}).get("Value") or ""
        if name:
            out.append({"name": name.strip(), "title": title, "since": o.get("since")})
    return out


def _shareholding(stock):
    out = []
    for cat in _as_list((stock or {}).get("shareholding")):
        if not isinstance(cat, dict):
            continue
        cats = _as_list(cat.get("categories"))
        latest = cats[-1] if cats else {}
        pct = _num(latest.get("percentage"))
        if pct is not None:
            out.append({"name": cat.get("displayName") or cat.get("categoryName"),
                        "pct": pct, "as_of": latest.get("holdingDate")})
    return out


def _shareholding_trend(stock):
    """Quarterly shareholding by category (Promoter / FII / DII / Public …) over
    the last ~6 quarters, so the Ownership tab can show how holdings are moving."""
    cats = _as_list((stock or {}).get("shareholding"))
    date_order, seen, series = [], set(), {}
    for cat in cats:
        if not isinstance(cat, dict):
            continue
        name = cat.get("displayName") or cat.get("categoryName")
        if not name:
            continue
        for entry in _as_list(cat.get("categories")):
            if not isinstance(entry, dict):
                continue
            d, p = entry.get("holdingDate"), _num(entry.get("percentage"))
            if not d or p is None:
                continue
            if d not in seen:
                seen.add(d); date_order.append(d)
            series.setdefault(name, {})[d] = p
    # Keep EVERY period the vendor reports — the tab renders the full history.
    periods = date_order
    rows = [{"name": n, "values": [series[n].get(d) for d in periods]} for n in series]
    return {"periods": periods, "rows": rows}


def _corporate_actions(stock):
    ca = (stock or {}).get("stockCorporateActionData") or {}
    out = []
    for kind, label in (("dividend", "Dividend"), ("splits", "Split"), ("bonus", "Bonus")):
        for row in _as_list(ca.get(kind))[:4]:
            if not isinstance(row, dict):
                continue
            out.append({
                "type": label,
                "date": row.get("date") or row.get("exDate") or row.get("recordDate"),
                "detail": row.get("purpose") or row.get("ratio") or row.get("dividend")
                          or row.get("value") or "",
            })
    return out[:8]


def _key_facts(stock):
    prof = (stock or {}).get("companyProfile") or {}
    d = (stock or {}).get("stockDetailsReusableData") or {}
    risk = (stock or {}).get("riskMeter") or {}
    return {
        "industry": prof.get("mgIndustry"),
        "isin": prof.get("isInId"),
        "nse_code": prof.get("exchangeCodeNse"),
        "bse_code": prof.get("exchangeCodeBse"),
        "market_cap_cr": _num(d.get("marketCap")),
        "year_high": _num(d.get("yhigh")),
        "year_low": _num(d.get("ylow")),
        "risk": risk.get("categoryName"),
        "rating": d.get("averageRating"),
    }


def _concalls(name):
    out = []
    for c in _as_list(_get("/concalls", {"stock_name": name}))[:8]:
        if not isinstance(c, dict):
            continue
        # IndianAPI returns the 'ai summary' as a PATH, not text. Resolving it
        # required extra synchronous upstream calls that made /profile hang and
        # burned quota — so we DON'T fetch it here. Pass text through only if it
        # already is text; drop bare paths (the frontend hides them anyway).
        summ = c.get("ai summary")
        if isinstance(summ, str) and summ.startswith("/"):
            summ = None
        out.append({"date": c.get("date"), "transcript": c.get("transcript"),
                    "ppt": c.get("ppt"), "rec": c.get("rec"),
                    "ai_summary": summ})
    return out


def _annual_reports(name):
    out = []
    for a in _as_list(_get("/annual_reports", {"stock_name": name}))[:8]:
        if isinstance(a, dict) and a.get("url"):
            out.append({"year": a.get("year"), "url": a.get("url"), "source": a.get("source")})
    return out


def _credit_ratings(name):
    out = []
    for c in _as_list(_get("/credit_ratings", {"stock_name": name}))[:6]:
        if isinstance(c, dict) and c.get("url"):
            out.append({"title": c.get("title"), "date": c.get("date"), "url": c.get("url")})
    return out


def _announcements(name):
    out = []
    for a in _as_list(_get("/recent_announcements", {"stock_name": name}))[:6]:
        if isinstance(a, dict):
            out.append({"title": a.get("title"), "link": a.get("link")})
    return out


_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
           "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def _q_key(label):
    try:
        mon, yr = str(label).split()
        return (int(yr), _MONTHS.get(mon[:3].lower(), 0))
    except Exception:
        return (0, 0)


def _periodic_pl(ticker: str, stats: str, n: int = 5):
    """Screener-style P&L from /historical_stats — shape {metric: {period:
    value}}. `stats=quarter_results` → quarters; `stats=yoy_results` → years.
    Returns the last `n` periods (newest-last) so quarterly & annual share one
    identical format."""
    data = _get("/historical_stats", {"stock_name": ticker, "stats": stats})
    if not isinstance(data, dict) or not data:
        return None
    labels = set()
    for series in data.values():
        if isinstance(series, dict):
            labels.update(series.keys())
    periods = sorted(labels, key=_q_key)[-n:]
    # Preserve IndianAPI's own line-item names AND order (sector-specific:
    # NBFCs get Revenue/Financing Profit/Financing Margin %, manufacturers get
    # Sales/Operating Profit/OPM %, etc.). No forced template.
    order = [name for name, series in data.items() if isinstance(series, dict)]
    metrics = {name: [data[name].get(p) for p in periods] for name in order}
    return {"periods": periods, "metrics": metrics, "order": order}


@router.get("/{ticker}/quarterly")
def company_quarterly(ticker: str, db: Session = Depends(get_db)):
    """Last 5 quarters of results, newest-last."""
    co = db.query(models.Company).filter_by(ticker=ticker.upper()).first()
    if not co:
        raise HTTPException(404, f"Unknown ticker {ticker}")
    r = _periodic_pl(ticker.upper(), "quarter_results")
    # keep legacy "quarters" key for the frontend
    return {"ticker": co.ticker, "has_data": bool(r and r["periods"]),
            "quarters": (r or {}).get("periods", []), "metrics": (r or {}).get("metrics", {}),
            "order": (r or {}).get("order", [])}


@router.get("/{ticker}/annual_pl")
def company_annual_pl(ticker: str, db: Session = Depends(get_db)):
    """Last 5 fiscal years of P&L in the SAME format as quarterly (yoy_results)."""
    co = db.query(models.Company).filter_by(ticker=ticker.upper()).first()
    if not co:
        raise HTTPException(404, f"Unknown ticker {ticker}")
    r = _periodic_pl(ticker.upper(), "yoy_results")
    return {"ticker": co.ticker, "has_data": bool(r and r["periods"]),
            "periods": (r or {}).get("periods", []), "metrics": (r or {}).get("metrics", {}),
            "order": (r or {}).get("order", [])}


def _periodic_response(co, stats, n=5):
    r = _periodic_pl(co.ticker, stats, n)
    return {"ticker": co.ticker, "has_data": bool(r and r["periods"]),
            "periods": (r or {}).get("periods", []), "metrics": (r or {}).get("metrics", {}),
            "order": (r or {}).get("order", [])}


@router.get("/{ticker}/balance_sheet")
def company_balance_sheet(ticker: str, db: Session = Depends(get_db)):
    """Annual balance sheet — original IndianAPI line items, last 5 years."""
    co = db.query(models.Company).filter_by(ticker=ticker.upper()).first()
    if not co:
        raise HTTPException(404, f"Unknown ticker {ticker}")
    return _periodic_response(co, "balancesheet")


@router.get("/{ticker}/cash_flow")
def company_cash_flow(ticker: str, db: Session = Depends(get_db)):
    """Annual cash flow — original IndianAPI line items, last 5 years."""
    co = db.query(models.Company).filter_by(ticker=ticker.upper()).first()
    if not co:
        raise HTTPException(404, f"Unknown ticker {ticker}")
    return _periodic_response(co, "cashflow")


@router.get("/{ticker}/ratios_live")
def company_ratios_live(ticker: str, db: Session = Depends(get_db)):
    """IndianAPI ratios published as-is (sector-specific), last 8 years."""
    co = db.query(models.Company).filter_by(ticker=ticker.upper()).first()
    if not co:
        raise HTTPException(404, f"Unknown ticker {ticker}")
    return _periodic_response(co, "ratios", n=8)


_SNAP_TTL_DAYS = 7      # profiles change slowly; weekly refresh per name


def _load_snapshot(ins):
    if ins is not None and isinstance(ins.data, dict):
        snap = ins.data.get("profile_snapshot")
        if isinstance(snap, dict) and isinstance(snap.get("payload"), dict):
            return snap
    return None


@router.get("/{ticker}/profile")
def company_profile(ticker: str, db: Session = Depends(get_db)):
    """Snapshot-first Business-tab profile.

    The old behaviour hit IndianAPI 5× per page view behind a 6-hour in-memory
    cache and NO budget guard — browsing alone could burn the whole 10k/month
    quota (it did, in July 2026: every profile served empty for days). Now the
    last good payload is persisted per company in the CompanyInsight blob and
    served for a week; the vendor is consulted only when the snapshot is stale
    AND the monthly budget allows, and a vendor failure degrades to the
    snapshot instead of an empty shell."""
    co = db.query(models.Company).filter_by(ticker=ticker.upper()).first()
    if not co:
        raise HTTPException(404, f"Unknown ticker {ticker}")

    name = ticker.upper()
    ins = db.query(models.CompanyInsight).filter_by(company_id=co.id).first()
    snap = _load_snapshot(ins)
    now = time.time()
    if snap and now - (snap.get("fetched_at") or 0) < _SNAP_TTL_DAYS * 86400:
        return snap["payload"]

    from app import api_budget

    payload = None
    if not api_budget.would_exceed(db, 5):
        before = _out_calls
        # Warm all 5 upstream calls concurrently (~3s vs ~15s sequential); the
        # parse helpers below then hit the in-memory cache instantly.
        # Third element: may this feed legitimately answer empty? Only /stock
        # may not (see _get) — an empty /stock for a ticker we track is upstream
        # trouble and must neither be cached for 6h nor read as a success.
        _warm = [("/stock", {"name": name}, False), ("/concalls", {"stock_name": name}, True),
                 ("/annual_reports", {"stock_name": name}, True), ("/credit_ratings", {"stock_name": name}, True),
                 ("/recent_announcements", {"stock_name": name}, True)]
        try:
            with ThreadPoolExecutor(max_workers=5) as ex:
                list(ex.map(lambda c: _get(c[0], c[1], empty_ok=c[2]), _warm))
        except Exception:
            pass
        stock = _get("/stock", {"name": name}, empty_ok=False) or {}
        prof = stock.get("companyProfile") or {}

        payload = {
            "ticker": co.ticker,
            "name": co.name,
            "description": prof.get("companyDescription"),
            "key_facts": _key_facts(stock),
            "leadership": _leadership(stock),
            "shareholding": _shareholding(stock),
            "shareholding_trend": _shareholding_trend(stock),
            "corporate_actions": _corporate_actions(stock),
            "concalls": _concalls(name),
            "annual_reports": _annual_reports(name),
            "credit_ratings": _credit_ratings(name),
            "announcements": _announcements(name),
        }
        # FIX-07: metering is now centralised in vendor_meter (ticked in _get,
        # flushed by the request middleware) — the local per-endpoint tally that
        # used to record_usage here would double-count, so it's removed.

        got_data = bool(prof) or any(
            payload.get(k) for k in ("leadership", "shareholding", "concalls",
                                     "annual_reports", "credit_ratings", "announcements"))
        if got_data:
            try:
                if ins is None:
                    ins = models.CompanyInsight(company_id=co.id, data={})
                    db.add(ins)
                data = dict(ins.data or {})
                data["profile_snapshot"] = {"fetched_at": now, "payload": payload}
                ins.data = data
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(ins, "data")
                db.commit()
            except Exception:
                db.rollback()
            return payload

    # Vendor failed or budget exhausted → last known good beats an empty shell.
    if snap:
        out = dict(snap["payload"])
        out["as_of"] = _dt_iso(snap.get("fetched_at"))
        return out
    return payload or {
        "ticker": co.ticker, "name": co.name, "description": None,
        "key_facts": _key_facts({}), "leadership": [], "shareholding": [],
        "shareholding_trend": [], "corporate_actions": [], "concalls": [],
        "annual_reports": [], "credit_ratings": [], "announcements": [],
    }


def _dt_iso(ts):
    try:
        import datetime
        return datetime.datetime.utcfromtimestamp(float(ts)).strftime("%Y-%m-%d")
    except Exception:
        return None
