"""
app/mf_routes.py — Mutual Fund board from the licensed vendor feed.

  GET /api/mutual-funds            → category → sub-category → [funds]
  GET /api/mutual-funds/search?q=  → name search
  GET /api/mutual-funds/{id}       → one scheme's detail + NAV history

The catalog carries fund name, latest NAV, 1-day % change and (where present)
asset size + star rating — the browsable universe every fund investor wants,
from the same feed the equity side uses. Cached 3h; the catalog is one call.
"""
import os
import time

import requests
from fastapi import APIRouter

router = APIRouter(prefix="/api/mutual-funds", tags=["mutual-funds"])

BASE = os.getenv("INDIANAPI_BASE", "https://stock.indianapi.in").rstrip("/")
KEY = os.getenv("INDIANAPI_KEY", "").strip()
_TTL = 3 * 3600
_cache: dict = {}


def _get(path, params=None, ttl=_TTL):
    ck = path + str(params or "")
    now = time.time()
    hit = _cache.get(ck)
    if hit and now - hit[0] < ttl:
        return hit[1]
    if not KEY:
        return None
    try:
        r = requests.get(BASE + path, headers={"X-API-Key": KEY, "x-api-key": KEY},
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


@router.get("/{scheme_id}")
def detail(scheme_id: str):
    det = _get("/mutual_funds_details", {"stock_name": scheme_id})
    hist = _get("/get_mf_historical_data", {"stock_id": scheme_id, "stats": "3M"})
    return {"detail": det if isinstance(det, dict) else None,
            "history": hist if isinstance(hist, (list, dict)) else None}
