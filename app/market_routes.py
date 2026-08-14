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
        from app import vendor_meter; vendor_meter.tick()  # FIX-07
        r = requests.get(BASE + path, headers={"X-API-Key": KEY, "x-api-key": KEY},
                         params=params or {}, timeout=20)
        data = r.json() if r.status_code == 200 else None
    except Exception:
        data = None
    # Record the OUTCOME, not just the spend. tick() above already counted the
    # quota this call burned — a failed call burns it too — so the meter alone
    # could not tell health that upstream had stopped answering.
    try:
        from app import vendor_meter as _vm   # explicit: the tick() import above
        _vm.record(data is not None)          # is local to the try block
    except Exception:
        pass
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


# A few IndianAPI endpoints (indices, commodities, mutual funds) live on the
# ANALYST host rather than the stock host we use for company data.
ANALYST_BASE = os.getenv("INDIANAPI_ANALYST_BASE", "https://analyst.indianapi.in").rstrip("/")


def _get_analyst(path, params=None, ttl=TTL):
    ck = "analyst:" + path + str(params or "")
    now = time.time()
    hit = _cache.get(ck)
    if hit and now - hit[0] < ttl:
        return hit[1]
    try:
        r = requests.get(ANALYST_BASE + path, headers={"X-API-Key": KEY, "x-api-key": KEY},
                         params=params or {}, timeout=20)
        data = r.json() if r.status_code == 200 else None
    except Exception:
        data = None
    if data is not None:
        _cache[ck] = (now, data)
        return data
    return hit[1] if hit else None


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
# NSE indices come from the Dhan live snapshot; BSE indices (SENSEX etc.) have
# no Dhan NSE-master mapping, so they're pulled from IndianAPI's /indices with
# exchange=BSE and shown with their vendor price/percentChange. Any index no
# feed returns is simply dropped (no blank cards — owner directive).
KEY_INDICES = [
    "NIFTY 50", "NIFTY Bank", "NIFTY Next 50", "NIFTY IT",
    "NIFTY Auto", "NIFTY Pharma", "NIFTY FMCG", "NIFTY Metal",
    "NIFTY FINANCIAL SERVICES", "NIFTY Midcap 100", "India VIX",
]
KEY_BSE_INDICES = [
    "SENSEX", "BSE Bankex", "BSE SENSEX 50", "BSE 100",
    "BSE MidCap", "BSE SmallCap", "BSE Auto", "BSE IT",
    "BSE Healthcare", "BSE FMCG", "BSE Oil & Gas", "BSE Realty",
]


def _bse_index_rows() -> dict:
    """{normalized_name: vendor_row} for BSE indices from IndianAPI's analyst
    /indices?exchange=BSE. Empty (and silent) when the host doesn't carry it."""
    out = {}
    try:
        data = _get_analyst("/indices", {"exchange": "BSE"})
        rows = data if isinstance(data, list) else ((data or {}).get("indices") or [])
        for it in rows:
            if isinstance(it, dict):
                out.setdefault(" ".join(str(it.get("name") or "").upper().split()), it)
    except Exception:
        pass
    return out


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
    # Change % + net come from IndianAPI's /indices, which lives on the
    # ANALYST host (the stock host 404s it). It carries percentChange/netChange
    # per index, so the dashboard cards can show live red/green movement.
    vendor = {}
    data = _get_analyst("/indices", {"exchange": "NSE"})
    rows = data if isinstance(data, list) else ((data or {}).get("indices") or [])
    for it in rows:
        if isinstance(it, dict):
            vendor.setdefault(" ".join(str(it.get("name") or "").upper().split()), it)
    out = []
    for nm in KEY_INDICES:
        key = " ".join(nm.upper().split())
        lv, vd = live.get(key), vendor.get(key)
        price = _num(lv) or _num((vd or {}).get("price"))
        if price is None:
            continue     # no feed has it → no blank card (owner directive)
        out.append({
            "name": nm, "price": price, "exchange": "NSE",
            "pct":   _num((vd or {}).get("percentChange")),
            "net":   _num((vd or {}).get("netChange")),
            "date":  (vd or {}).get("date"), "time": (vd or {}).get("time"),
        })
    # BSE indices (SENSEX et al.) — vendor-priced, appended after the NSE set.
    bse = _bse_index_rows()
    for nm in KEY_BSE_INDICES:
        vd = bse.get(" ".join(nm.upper().split()))
        price = _num((vd or {}).get("price"))
        if price is None:
            continue
        out.append({
            "name": nm, "price": price, "exchange": "BSE",
            "pct":   _num(vd.get("percentChange")),
            "net":   _num(vd.get("netChange")),
            "date":  vd.get("date"), "time": vd.get("time"),
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


def _active_from_db(limit: int = 10):
    """Most-active by traded volume from OUR own Dhan-fed EOD history.

    The IndianAPI most-active feed is quota-throttled for stretches, and `_get`
    serves the last good payload on failure — so the vendor list could sit
    frozen for days. Our HistoricalPrice table is topped up every evening by the
    Dhan EOD job (close + volume), so ranking the latest trading day by volume
    gives a fresh most-active list that never depends on the vendor quota."""
    try:
        from app.database import SessionLocal
        from app import models
        from sqlalchemy import func
    except Exception:
        return []
    s = SessionLocal()
    try:
        d0 = s.query(func.max(models.HistoricalPrice.date)).scalar()
        if not d0:
            return []
        # Rank by traded VALUE (turnover = close × volume), not raw share count —
        # "most active" on a professional desk means value, which surfaces liquid
        # large/mid-caps rather than a penny stock with a huge share count. We
        # can't sort by the product in SQL portably, so pull the top volume rows
        # (a superset — the highest-turnover names are always high-volume too)
        # and re-rank in Python by value.
        rows = (s.query(models.HistoricalPrice.company_id,
                        models.HistoricalPrice.close,
                        models.HistoricalPrice.volume)
                  .filter(models.HistoricalPrice.date == d0,
                          models.HistoricalPrice.volume.isnot(None),
                          models.HistoricalPrice.volume > 0,
                          models.HistoricalPrice.close.isnot(None))
                  .order_by(models.HistoricalPrice.volume.desc())
                  .limit(120).all())
        if not rows:
            return []
        rows = sorted(rows, key=lambda r: (r.close or 0) * (r.volume or 0), reverse=True)
        top = rows[:limit + 5]
        cos = {c.id: c for c in s.query(models.Company)
               .filter(models.Company.id.in_([t.company_id for t in top])).all()}
        out = []
        for t in top:
            co = cos.get(t.company_id)
            if not co:
                continue
            prev = (s.query(models.HistoricalPrice.close)
                      .filter(models.HistoricalPrice.company_id == t.company_id,
                              models.HistoricalPrice.date < d0)
                      .order_by(models.HistoricalPrice.date.desc())
                      .first())
            pct = None
            if prev and prev[0] and t.close is not None:
                try:
                    pct = round((t.close - prev[0]) / prev[0] * 100, 2)
                except ZeroDivisionError:
                    pct = None
            out.append({
                "name": co.name, "ticker": co.ticker.upper(),
                "price": t.close, "pct": pct, "volume": t.volume,
                "value_cr": round(t.close * t.volume / 1e7, 1),  # ₹ crore traded
                "rating": None,
            })
            if len(out) >= limit:
                break
        return out
    finally:
        s.close()


def _vendor_active(path: str):
    """IndianAPI most-active for `path` (/NSE_most_active | /BSE_most_active),
    kept as a fallback for when our own EOD history isn't available."""
    out = []
    for x in (_get(path) or []):
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


def _active():
    # Our fresh EOD-derived list is authoritative; vendor is the fallback.
    return _active_from_db() or _vendor_active("/NSE_most_active")


def _bse_active():
    # We only maintain NSE EOD volume, so BSE prefers the vendor's own list and
    # falls back to the fresh NSE-derived ranking rather than sitting stale.
    return _vendor_active("/BSE_most_active") or _active_from_db()


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


@router.get("/bse_active")
def bse_active():
    return {"bse_active": _bse_active()}


@router.get("/high_low")
def high_low():
    return _high_low()


_COMMODITY_SHOW = ("GOLD", "SILVER", "CRUDEOIL", "NATURALGAS", "COPPER")


def _commodities():
    """Headline MCX futures (gold/silver/crude/natgas/copper) — the front-month
    contract per product, with last price and % change."""
    data = _get_analyst("/commodities")
    rows = data if isinstance(data, list) else ((data or {}).get("commodities") or [])
    best = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        prod = str(r.get("product") or "").upper().replace(" ", "")
        if prod not in _COMMODITY_SHOW:
            continue
        # keep the nearest expiry (front month) per product
        if prod not in best or str(r.get("expiry") or "") < str(best[prod].get("expiry") or "z"):
            best[prod] = r
    out = []
    for prod in _COMMODITY_SHOW:
        r = best.get(prod)
        if not r:
            continue
        out.append({"name": prod.title(), "price": _num(r.get("last_traded_price")),
                    "pct": _num(r.get("per_change")), "net": _num(r.get("change")),
                    "expiry": r.get("expiry")})
    return out


@router.get("/commodities")
def commodities():
    return {"commodities": _commodities()}


@router.get("/snapshot")
def snapshot():
    """Everything the dashboard needs in one round-trip — the 4 feeds are
    fetched concurrently so the dashboard loads in ~1 upstream round-trip."""
    try:
        with ThreadPoolExecutor(max_workers=4) as ex:
            f_idx = ex.submit(_indices); f_mov = ex.submit(_movers)
            f_act = ex.submit(_active);  f_hl = ex.submit(_high_low)
            f_com = ex.submit(_commodities); f_bse = ex.submit(_bse_active)
            indices, movers, active, high_low = f_idx.result(), f_mov.result(), f_act.result(), f_hl.result()
            commodities_l = f_com.result(); bse_l = f_bse.result()
    except Exception:
        indices, movers, active, high_low = _indices(), _movers(), _active(), _high_low()
        commodities_l = _commodities(); bse_l = _bse_active()
    return {
        "indices": indices, "movers": movers, "active": active,
        "bse_active": bse_l, "high_low": high_low, "commodities": commodities_l,
        "as_of": time.strftime("%Y-%m-%d %H:%M"),
    }
