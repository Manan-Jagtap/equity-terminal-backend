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
import os, re, time, requests
from concurrent.futures import ThreadPoolExecutor
from fastapi import APIRouter

router = APIRouter(prefix="/api/market")

BASE = os.getenv("INDIANAPI_BASE", "https://stock.indianapi.in").rstrip("/")
KEY = os.getenv("INDIANAPI_KEY", "").strip()
TTL = 900  # seconds — market feeds change slowly; 15-min cache keeps loads fast

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


# ── Universe restriction ─────────────────────────────────────────────────────
# The vendor feeds are NSE-WIDE; the terminal only covers its Nifty-500
# universe. Every list is filtered to names we actually cover (mapped to OUR
# ticker so rows are clickable), and anything else is dropped — commenting on
# stocks outside coverage was showing Scan Steels / Ruby Mills on the
# dashboard with no page behind them.
_UNI_TTL = 3600
_uni_cache: dict = {"ts": 0, "by_ticker": {}, "by_name": {}}
_NAME_STOP = re.compile(
    r"\b(ltd|limited|company|corp|corporation|industries|enterprises|the|and)\b\.?", re.I)


def _norm_name(n: str) -> str:
    n = _NAME_STOP.sub(" ", (n or "").lower().replace("&", " and "))
    return " ".join(re.split(r"[^a-z0-9]+", n)).strip()


def _universe():
    now = time.time()
    if now - _uni_cache["ts"] < _UNI_TTL and _uni_cache["by_ticker"]:
        return _uni_cache
    try:
        from app.database import SessionLocal
        from app import models
        s = SessionLocal()
        try:
            rows = s.query(models.Company.ticker, models.Company.name).all()
        finally:
            s.close()
        by_ticker = {t.upper(): t.upper() for t, _ in rows}
        by_name = {_norm_name(nm): t.upper() for t, nm in rows if nm}
        _uni_cache.update(ts=now, by_ticker=by_ticker, by_name=by_name)
    except Exception:
        pass
    return _uni_cache


def _to_universe(row) -> str | None:
    """Map a vendor row to OUR ticker, or None when outside coverage."""
    uni = _universe()
    for key in ("nseCode", "nse_code"):
        v = (row.get(key) or "").strip().upper()
        if v and v in uni["by_ticker"]:
            return v
    ric = (row.get("ticker") or row.get("ric") or "").strip().upper()
    if ric:
        sym = ric.split(".")[0]
        if sym in uni["by_ticker"]:
            return sym
    nm = _norm_name(row.get("company_name") or row.get("company") or row.get("name") or "")
    if nm:
        hit = uni["by_name"].get(nm)
        if hit:
            return hit
        # The vendor often drops suffixes ("Sun Pharmaceutical" vs "Sun
        # Pharmaceutical Industries"). Prefixes must align on a WORD boundary
        # and the shorter side must be ≥6 chars — a bare startswith mapped
        # "Pioneer Investcorp" onto PI Industries via the 2-char stem "pi".
        if len(nm.split()) >= 2:
            for full, tk in uni["by_name"].items():
                if min(len(full), len(nm)) < 6:
                    continue
                if full.startswith(nm + " ") or nm.startswith(full + " "):
                    return tk
    return None


# Curated, ordered set of headline indices for the dashboard strip.
KEY_INDICES = [
    "NIFTY 50", "NIFTY Bank", "SENSEX", "NIFTY Next 50", "NIFTY IT",
    "NIFTY Auto", "NIFTY Pharma", "NIFTY FMCG", "NIFTY Metal",
    "NIFTY FINANCIAL SERVICES", "NIFTY Midcap 100", "India VIX",
]


def _indices():
    """Headline indices. The production IndianAPI host has no /indices, so
    values come from the Dhan live snapshot when the feed is up; the list of
    NAMES always returns so every index stays a clickable chart entry even
    when live values are unavailable."""
    live = {}
    try:
        from app.live_prices import snapshot as _live_snap
        # shape: {display_name: last_price_float}
        for nm, px in (_live_snap().get("indices") or {}).items():
            live[" ".join(str(nm).upper().split())] = px
    except Exception:
        pass
    vendor = {}
    data = _get("/indices") or {}
    for it in (data.get("indices") or []):
        vendor.setdefault(" ".join(str(it.get("name") or "").upper().split()), it)
    out = []
    for nm in KEY_INDICES:
        key = " ".join(nm.upper().split())
        lv, vd = live.get(key), vendor.get(key)
        out.append({
            "name": nm,
            "price": _num(lv) or _num((vd or {}).get("price")),
            "pct":   _num((vd or {}).get("percentChange")),
            "net":   _num((vd or {}).get("netChange")),
            "date":  (vd or {}).get("date"), "time": (vd or {}).get("time"),
        })
    return out


def _movers():
    data = _get("/trending") or {}
    ts = data.get("trending_stocks") or {}

    def clean(arr):
        out = []
        for x in (arr or []):
            tk = _to_universe(x)
            if not tk:
                continue          # outside our coverage — never listed
            out.append({
                "name": x.get("company_name"), "ticker": tk,
                "price": _num(x.get("price")), "pct": _num(x.get("percent_change")),
                "rating": x.get("overall_rating"),
            })
            if len(out) >= 8:
                break
        return out

    return {"gainers": clean(ts.get("top_gainers")), "losers": clean(ts.get("top_losers"))}


def _active():
    data = _get("/NSE_most_active") or []
    out = []
    for x in (data or []):
        tk = _to_universe(x)
        if not tk:
            continue
        out.append({
            "name": x.get("company"), "ticker": tk,
            "price": _num(x.get("price")), "pct": _num(x.get("percent_change")),
            "volume": _num(x.get("volume")), "rating": x.get("overall_rating"),
        })
        if len(out) >= 10:
            break
    return out


def _high_low():
    data = _get("/fetch_52_week_high_low_data") or {}
    nse = data.get("NSE_52WeekHighLow") or {}

    def clean(arr, level_key):
        out = []
        for x in (arr or []):
            tk = _to_universe(x)
            if not tk:
                continue
            out.append({
                "name": x.get("company"), "ticker": tk,
                "price": _num(x.get("price")), "level": _num(x.get(level_key)),
            })
            if len(out) >= 8:
                break
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
    """Everything the dashboard needs in one round-trip — the 4 feeds are
    fetched concurrently so the dashboard loads in ~1 upstream round-trip."""
    try:
        with ThreadPoolExecutor(max_workers=4) as ex:
            f_idx = ex.submit(_indices); f_mov = ex.submit(_movers)
            f_act = ex.submit(_active);  f_hl = ex.submit(_high_low)
            indices, movers, active, high_low = f_idx.result(), f_mov.result(), f_act.result(), f_hl.result()
    except Exception:
        indices, movers, active, high_low = _indices(), _movers(), _active(), _high_low()
    return {
        "indices": indices, "movers": movers, "active": active,
        "high_low": high_low, "as_of": time.strftime("%Y-%m-%d %H:%M"),
    }
