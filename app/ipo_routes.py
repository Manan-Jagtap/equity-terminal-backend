"""
app/ipo_routes.py — IPO board from the licensed vendor feed.

  GET /api/ipo → { upcoming, active, closed, listed, as_of }

Each row: name, symbol, SME flag, price band, lot size, bidding/allotment/
listing dates, total subscription rate, listing price + gains, RHP link.
Cached 3h in-process (~8 vendor calls/day — negligible against the budget).

GMP (grey-market premium) is deliberately NOT published: it is unregulated
rumor-market data no licensed source carries; the verifiable analogues here
are the subscription rate and realised listing gains.
"""
import os
import time

import requests
from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["ipo"])

BASE = os.getenv("INDIANAPI_BASE", "https://stock.indianapi.in").rstrip("/")
KEY = os.getenv("INDIANAPI_KEY", "").strip()
_TTL = 3 * 3600
_cache: dict = {"ts": 0.0, "data": None}

_FIELDS = ("symbol", "name", "is_sme", "status", "min_price", "max_price",
           "issue_price", "lot_size", "min_bid_quantity",
           "bidding_start_date", "bidding_end_date", "allotment_date",
           "listing_date", "listing_price", "listing_gains",
           "total_subscription_rate", "additional_text", "document_url")


def _clean(rows):
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        out.append({k: r.get(k) for k in _FIELDS})
    return out


@router.get("/ipo")
def ipo_board():
    now = time.time()
    if _cache["data"] is not None and now - _cache["ts"] < _TTL:
        return _cache["data"]
    data = None
    if KEY:
        try:
            resp = requests.get(BASE + "/ipo",
                                headers={"X-API-Key": KEY, "x-api-key": KEY},
                                timeout=25)
            if resp.status_code == 200:
                data = resp.json()
        except Exception:
            data = None
    if not isinstance(data, dict):
        # serve last-known-good over an empty shell
        if _cache["data"] is not None:
            return _cache["data"]
        return {"upcoming": [], "active": [], "closed": [], "listed": [],
                "as_of": None, "available": False}
    payload = {
        "upcoming": _clean(data.get("upcoming")),
        "active":   _clean(data.get("active")),
        "closed":   _clean(data.get("closed")),
        "listed":   _clean(data.get("listed")),
        "as_of": time.strftime("%Y-%m-%d %H:%M"),
        "available": True,
    }
    _cache.update(ts=now, data=payload)
    return payload
