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
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app import models

router = APIRouter(prefix="/api/companies")

BASE = os.getenv("INDIANAPI_BASE", "https://dev.indianapi.in").rstrip("/")
KEY = os.getenv("INDIANAPI_KEY", "").strip()
TTL = 6 * 3600  # profiles change slowly

_cache: dict[str, tuple[float, object]] = {}


def _get(path, params, ttl=TTL):
    ck = path + str(params)
    now = time.time()
    hit = _cache.get(ck)
    if hit and now - hit[0] < ttl:
        return hit[1]
    if not KEY:
        return None
    try:
        r = requests.get(BASE + path, headers={"X-API-Key": KEY, "x-api-key": KEY},
                         params=params, timeout=25)
        data = r.json() if r.status_code == 200 else None
    except Exception:
        data = None
    if data is not None:
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
        out.append({"date": c.get("date"), "transcript": c.get("transcript"),
                    "ppt": c.get("ppt"), "rec": c.get("rec"),
                    "ai_summary": c.get("ai summary")})
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
    metrics = {name: [series.get(p) for p in periods]
               for name, series in data.items() if isinstance(series, dict)}
    return {"periods": periods, "metrics": metrics}


@router.get("/{ticker}/quarterly")
def company_quarterly(ticker: str, db: Session = Depends(get_db)):
    """Last 5 quarters of results, newest-last."""
    co = db.query(models.Company).filter_by(ticker=ticker.upper()).first()
    if not co:
        raise HTTPException(404, f"Unknown ticker {ticker}")
    r = _periodic_pl(ticker.upper(), "quarter_results")
    # keep legacy "quarters" key for the frontend
    return {"ticker": co.ticker, "has_data": bool(r and r["periods"]),
            "quarters": (r or {}).get("periods", []), "metrics": (r or {}).get("metrics", {})}


@router.get("/{ticker}/annual_pl")
def company_annual_pl(ticker: str, db: Session = Depends(get_db)):
    """Last 5 fiscal years of P&L in the SAME format as quarterly (yoy_results)."""
    co = db.query(models.Company).filter_by(ticker=ticker.upper()).first()
    if not co:
        raise HTTPException(404, f"Unknown ticker {ticker}")
    r = _periodic_pl(ticker.upper(), "yoy_results")
    return {"ticker": co.ticker, "has_data": bool(r and r["periods"]),
            "periods": (r or {}).get("periods", []), "metrics": (r or {}).get("metrics", {})}


@router.get("/{ticker}/profile")
def company_profile(ticker: str, db: Session = Depends(get_db)):
    co = db.query(models.Company).filter_by(ticker=ticker.upper()).first()
    if not co:
        raise HTTPException(404, f"Unknown ticker {ticker}")

    name = ticker.upper()
    stock = _get("/stock", {"name": name}) or {}
    prof = stock.get("companyProfile") or {}

    return {
        "ticker": co.ticker,
        "name": co.name,
        "description": prof.get("companyDescription"),
        "key_facts": _key_facts(stock),
        "leadership": _leadership(stock),
        "shareholding": _shareholding(stock),
        "corporate_actions": _corporate_actions(stock),
        "concalls": _concalls(name),
        "annual_reports": _annual_reports(name),
        "credit_ratings": _credit_ratings(name),
        "announcements": _announcements(name),
    }
