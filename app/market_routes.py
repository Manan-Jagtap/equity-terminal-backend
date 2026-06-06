"""
market_routes.py — live Market Dashboard data from IndianAPI v2.

These are live/market-wide feeds (not per-company fundamentals), so they're
proxied directly from IndianAPI with a short in-memory TTL cache. The cache
keeps us well within the monthly quota even if the dashboard is left open and
auto-refreshing, and serves the last good payload if an upstream call fails.

  GET /api/market/indices    → key indices (Nifty 50, Bank, Sensex, sectors…)
  GET /api/market/movers     → top gainers / losers
  GET /api/market/active     → NSE most-active
  GET /api/market/high_low   → 52-week highs / lows (NSE)
  GET /api/market/snapshot   → all of the above in one call (one round-trip)
"""
import os, time, requests
from fastapi import APIRouter

router = APIRouter(prefix="/api/market")

BASE = os.getenv("INDIANAPI_BASE", "https://dev.indianapi.in").rstrip("/")
KEY = os.getenv("INDIANAPI_KEY", "").strip()
TTL = 120  # seconds — market feeds refresh ~once a minute; 2-min cache is plenty

_cache = {}  # key -> (ts, data)


def _get(path, params=None, ttl=TTL):
    ck = path + str(params or "")
    now = time.time()
    hit = _cache.get(ck)
    if hit and now - hit[0] < ttl:
        return hit[1]
    try:
        r = requests.get(BASE + path, headers={"X-API-Key": KEY, "x-api-key": KEY},
                         params=params or {}, timeout=20)
        data = r.json() if r.status_code == 200 else None
    except Exception:
        data = None
    if data is not None:
        _cache[ck] = (now, data)
        return data
    return hit[1] if hit else None   # serve stale on upstream failure


def _num(x):
    if x is None:
        return None
    try:
        return float(str(x).replace(",", "").replace("%", "").strip())
    except (ValueError, AttributeError):
        return None


# Curated, ordered set of headline indices for the dashboard strip.
KEY_INDICES = [
    "NIFTY 50", "NIFTY Bank", "SENSEX", "NIFTY Next 50", "NIFTY IT",
    "NIFTY Auto", "NIFTY Pharma", "NIFTY FMCG", "NIFTY Metal",
    "NIFTY FINANCIAL SERVICES", "NIFTY Midcap 100", "India VIX",
]


def _indices():
    data = _get("/indices") or {}
    items = data.get("indices") or []
    by_name = {}
    for it in items:
        by_name.setdefault(it.get("name"), it)
    out = []
    for nm in KEY_INDICES:
        it = by_name.get(nm)
        if it:
            out.append({
                "name": nm, "price": _num(it.get("price")),
                "pct": _num(it.get("percentChange")), "net": _num(it.get("netChange")),
                "date": it.get("date"), "time": it.get("time"),
            })
    return out


def _movers():
    data = _get("/trending") or {}
    ts = data.get("trending_stocks") or {}

    def clean(arr):
        out = []
        for x in (arr or [])[:8]:
            out.append({
                "name": x.get("company_name"), "ticker": x.get("nseCode") or x.get("ric"),
                "price": _num(x.get("price")), "pct": _num(x.get("percent_change")),
                "rating": x.get("overall_rating"),
            })
        return out

    return {"gainers": clean(ts.get("top_gainers")), "losers": clean(ts.get("top_losers"))}


def _active():
    data = _get("/NSE_most_active") or []
    out = []
    for x in (data or [])[:10]:
        out.append({
            "name": x.get("company"), "ticker": x.get("ticker"),
            "price": _num(x.get("price")), "pct": _num(x.get("percent_change")),
            "volume": _num(x.get("volume")), "rating": x.get("overall_rating"),
        })
    return out


def _high_low():
    data = _get("/fetch_52_week_high_low_data") or {}
    nse = data.get("NSE_52WeekHighLow") or {}

    def clean(arr, level_key):
        out = []
        for x in (arr or [])[:8]:
            out.append({
                "name": x.get("company"), "ticker": x.get("ticker"),
                "price": _num(x.get("price")), "level": _num(x.get(level_key)),
            })
        return out

    return {
        "highs": clean(nse.get("high52Week"), "52_week_high"),
        "lows": clean(nse.get("low52Week"), "52_week_low"),
    }


@router.get("/indices")
def indices():
    return {"indices": _indices()}


@router.get("/movers")
def movers():
    return _movers()


@router.get("/active")
def active():
    return {"active": _active()}


@router.get("/high_low")
def high_low():
    return _high_low()


@router.get("/snapshot")
def snapshot():
    """Everything the dashboard needs in one round-trip."""
    return {
        "indices": _indices(),
        "movers": _movers(),
        "active": _active(),
        "high_low": _high_low(),
        "as_of": time.strftime("%Y-%m-%d %H:%M"),
    }
