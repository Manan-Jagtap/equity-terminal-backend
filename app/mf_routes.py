"""
app/mf_routes.py — Mutual Fund board from the licensed vendor feed.

  GET /api/mutual-funds            → category → sub-category → [funds]
  GET /api/mutual-funds/search?q=  → name search
  GET /api/mutual-funds/detail?name=&range=  → holdings + NAV + facts
  GET /api/mutual-funds/nav?id=&range=       → NAV series only (range toggle)

The catalog carries fund name, latest NAV, 1-day % change, asset size, star
rating and trailing returns (1M/3M/6M/1Y/3Y) — the browsable universe every
fund investor wants, from the same feed the equity side uses. Cached 3h; the
catalog is one call.
"""
import os
import time

import requests
from fastapi import APIRouter

router = APIRouter(prefix="/api/mutual-funds", tags=["mutual-funds"])

BASE = os.getenv("INDIANAPI_BASE", "https://stock.indianapi.in").rstrip("/")
ANALYST_BASE = os.getenv("INDIANAPI_ANALYST_BASE", "https://analyst.indianapi.in").rstrip("/")
KEY = os.getenv("INDIANAPI_KEY", "").strip()
_TTL = 3 * 3600
_cache: dict = {}


def _get(path, params=None, ttl=_TTL, host=None):
    ck = (host or "") + path + str(params or "")
    now = time.time()
    hit = _cache.get(ck)
    if hit and now - hit[0] < ttl:
        return hit[1]
    if not KEY:
        return None
    try:
        r = requests.get((host or BASE) + path, headers={"X-API-Key": KEY, "x-api-key": KEY},
                         params=params or {}, timeout=25)
        data = r.json() if r.status_code == 200 else None
    except Exception:
        data = None
    if data is not None:
        _cache[ck] = (now, data)
        return data
    return hit[1] if hit else None


@router.get("")
def catalog():
    """Full browsable catalog: {category: {sub_category: [funds]}}.
    Funds are trimmed to the display fields and sorted by 1-day change."""
    data = _get("/mutual_funds")
    if not isinstance(data, dict):
        return {"categories": [], "available": False}
    cats = []
    for cat, subs in data.items():
        if not isinstance(subs, dict):
            continue
        sub_out = []
        n_funds = 0
        for sub, funds in subs.items():
            rows = []
            for f in (funds or []):
                if not isinstance(f, dict):
                    continue
                rows.append({
                    "name": f.get("fund_name") or f.get("name"),
                    "nav": f.get("latest_nav"),
                    "change": f.get("percentage_change"),
                    "asset_size": f.get("asset_size"),
                    "rating": f.get("star_rating") or f.get("rating"),
                    # Trailing returns the vendor already ships per fund — surfaced
                    # so the list can sort/filter on performance, not just NAV.
                    "r1m": f.get("1_month_return"),
                    "r3m": f.get("3_month_return"),
                    "r6m": f.get("6_month_return"),
                    "r1y": f.get("1_year_return"),
                    "r3y": f.get("3_year_return"),
                    "sub_category": sub,
                    "category": cat,
                })
            rows.sort(key=lambda r: (r["change"] if isinstance(r["change"], (int, float)) else -1e9),
                      reverse=True)
            n_funds += len(rows)
            sub_out.append({"name": sub, "funds": rows})
        sub_out.sort(key=lambda s: -len(s["funds"]))
        cats.append({"name": cat, "sub_categories": sub_out, "count": n_funds})
    cats.sort(key=lambda c: -c["count"])
    return {"categories": cats, "as_of": time.strftime("%Y-%m-%d %H:%M"), "available": True}


@router.get("/search")
def search(q: str):
    data = _get("/mutual_fund_search", {"query": q}, ttl=900)
    rows = data if isinstance(data, list) else ((data or {}).get("data") or [])
    out = []
    for r in rows[:25]:
        if isinstance(r, dict):
            out.append({"id": r.get("id") or r.get("schemeCode"),
                        "name": r.get("schemeName") or r.get("fund_name") or r.get("name"),
                        "isin": r.get("isin")})
    return {"results": out}


_RANGES = ("1M", "3M", "6M", "1Y", "3Y", "5Y", "MAX")


def _resolve(name):
    """Map a catalog fund name to its vendor scheme record via search.
    The catalog name carries plan suffixes ("… Regular Growth") the vendor
    search chokes on; strip them and fall back to progressively shorter
    queries until a scheme matches."""
    import re as _re
    cleaned = _re.sub(r"\b(regular|direct|growth|plan|dividend|idcw|payout|reinvestment|option|fund)\b",
                      " ", name, flags=_re.I)
    cleaned = " ".join(cleaned.split())
    words = cleaned.split()
    tries = [cleaned] + [" ".join(words[:k]) for k in (3, 2)] + [name]
    for qy in tries:
        if not qy:
            continue
        hits = _get("/mutual_fund_search", {"query": qy}, ttl=1800)
        rows = hits if isinstance(hits, list) else ((hits or {}).get("data") or [])
        if rows:
            return rows[0]
    return None


def _nav_series(sid, rng):
    stats = rng if rng in _RANGES else "1Y"
    hist = _get("/get_mf_historical_data", {"stock_id": sid, "stats": stats}, host=ANALYST_BASE) or []
    return stats, [{"date": p.get("date"), "nav": p.get("nav")}
                   for p in (hist if isinstance(hist, list) else [])
                   if isinstance(p, dict) and p.get("nav") is not None]


# Vendor detail shapes vary; pull known facts under stable keys, best-effort.
_INFO_KEYS = {
    "fund_manager": ("fund_manager", "fundManager", "manager", "fund_managers"),
    "expense_ratio": ("expense_ratio", "expenseRatio", "expense"),
    "launch_date": ("launch_date", "inception_date", "inceptionDate", "launchDate"),
    "benchmark": ("benchmark", "benchmark_name", "benchmarkName"),
    "category": ("category", "categoryName", "category_name"),
    "aum": ("aum", "fund_size", "asset_size", "fundSize"),
    "exit_load": ("exit_load", "exitLoad"),
    "min_investment": ("min_investment", "minInvestment", "min_lumpsum", "minimum_investment"),
    "min_sip": ("min_sip", "minSip", "minimum_sip"),
    "risk": ("risk", "riskometer", "risk_rating", "riskLevel"),
    "lock_in": ("lock_in", "lockIn", "lock_in_period"),
}


@router.get("/detail")
def detail(name: str, range: str = "1Y"):
    """Everything we have on one scheme: resolve the name to its scheme id via
    search, then pull the portfolio holdings, NAV history for the requested
    range, and (best-effort) the vendor's fund-facts block."""
    match = _resolve(name)
    if not match:
        return {"available": False, "name": name}
    sid = match.get("id") or match.get("schemeCode")
    scheme_name = match.get("schemeName") or name

    holdings = _get("/mf_holdings", {"stock_id": sid}, host=ANALYST_BASE) or []
    rng, nav_out = _nav_series(sid, range)

    hold_out = []
    for h in (holdings if isinstance(holdings, list) else []):
        if isinstance(h, dict) and h.get("name"):
            try:
                alloc = float(h.get("allocation")) if h.get("allocation") is not None else None
            except (ValueError, TypeError):
                alloc = None
            hold_out.append({"name": h["name"], "allocation": alloc,
                             "value": h.get("value"),
                             "sector": h.get("sector") or h.get("industry"),
                             "instrument": h.get("instrument_type") or h.get("type") or h.get("asset_type")})
    hold_out.sort(key=lambda x: (x["allocation"] if x["allocation"] is not None else -1), reverse=True)
    top10 = round(sum((h["allocation"] or 0) for h in hold_out[:10]), 1) if hold_out else None

    # Best-effort richer facts. This endpoint is finicky about identifiers, so a
    # miss is expected and simply yields an empty facts block — the page stands
    # on holdings + NAV + the catalog returns either way.
    info = {}
    dts = _get("/mutual_funds_details", {"stock_name": scheme_name}, ttl=1800)
    src = None
    if isinstance(dts, dict):
        src = dts.get("data") if isinstance(dts.get("data"), dict) else dts
    if isinstance(src, dict):
        for out_k, keys in _INFO_KEYS.items():
            for k in keys:
                v = src.get(k)
                if v not in (None, "", []):
                    info[out_k] = v
                    break

    return {"available": True,
            "name": scheme_name,
            "isin": match.get("isin"), "id": sid,
            "scheme_type": match.get("schemeType"),
            "category_id": match.get("categoryId"),
            "range": rng,
            "holdings": hold_out, "nav_history": nav_out,
            "top10_weight": top10, "holdings_count": len(hold_out),
            "info": info}


@router.get("/nav")
def nav_history(id: str, range: str = "1Y"):
    """Just the NAV series for one scheme id and range — powers the detail
    page's range toggle without re-resolving the whole fund."""
    rng, nav_out = _nav_series(id, range)
    return {"id": id, "range": rng, "nav_history": nav_out}
