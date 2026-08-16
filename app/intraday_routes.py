"""
app/intraday_routes.py — 1-day intraday tick series from IndianAPI.

  GET /api/companies/{ticker}/intraday → { values: [{t, price}], change, pct, prev }

The vendor's /1D_intraday_data (analyst host, keyed by the IndianAPI ticker_id
we store on CompanyInsight) returns the day's minute ticks. Cached 3 min so an
open chart doesn't hammer the quota. Empty (not an error) when the market is
closed or the name has no ticker_id.

Every empty return that is NOT the healthy closed-market case carries a
"reason" ("unknown_ticker" | "no_feed" | "quota" | "vendor_error"). Without it
the frontend could only render all four causes as "the market is closed" —
turning quota exhaustion and vendor outages into a market-hours FACT the
server never asserted. The healthy-but-empty payload stays unlabeled on
purpose: absence of "reason" is the signal that emptiness is trustworthy.
"""
import os
import time

import requests
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app import models

router = APIRouter(prefix="/api/companies", tags=["intraday"])

ANALYST_BASE = os.getenv("INDIANAPI_ANALYST_BASE", "https://analyst.indianapi.in").rstrip("/")
KEY = os.getenv("INDIANAPI_KEY", "").strip()
_TTL = 180
_cache: dict = {}


@router.get("/{ticker}/intraday")
def intraday(ticker: str, db: Session = Depends(get_db)):
    tk = ticker.upper()
    now = time.time()
    hit = _cache.get(tk)
    if hit and now - hit[0] < _TTL:
        return hit[1]

    co = db.query(models.Company).filter_by(ticker=tk).first()
    if not co:
        return {"ticker": tk, "available": False, "values": [], "reason": "unknown_ticker"}
    ins = db.query(models.CompanyInsight).filter_by(company_id=co.id).first()
    tid = ins.ticker_id if ins else None
    if not tid or not KEY:
        return {"ticker": tk, "available": False, "values": [], "reason": "no_feed"}

    # This route is unauthenticated and the 3-minute cache is keyed per TICKER,
    # so cycling the ~1000-name universe can spend ~20k vendor calls an hour —
    # against a Developer-plan ceiling of 10k a MONTH. Worse, the call was never
    # ticked into vendor_meter, so api_budget could not see it: the module
    # docstring's own warning that "the budget guard governed on ~10-15% of real
    # spend" still applied to this call site.
    try:
        from app import api_budget
        if api_budget.would_exceed(db, 1):
            stale = _cache.get(tk)
            if stale:
                return stale[1]
            return {"ticker": tk, "available": False, "values": [], "reason": "quota",
                    "note": "vendor quota exhausted for this month"}
    except Exception:
        pass          # fail OPEN — a broken guard must not take the chart down

    try:
        from app import vendor_meter; vendor_meter.tick()   # FIX-07: was unmetered
        r = requests.post(ANALYST_BASE + "/1D_intraday_data",
                          headers={"x-api-key": KEY}, params={"stock_id": tid}, timeout=20)
        data = r.json() if r.status_code == 200 else None
    except Exception:
        data = None

    row = None
    if isinstance(data, list) and data:
        row = data[0]
    elif isinstance(data, dict):
        row = data
    # OUTCOME (vendor_meter.record), judged on the route's OWN boundary:
    # whatever the code below cannot read as a row is what it labels
    # "vendor_error"; a row with no ticks is the healthy closed-market answer
    # and must not read as a failure. Deliberately not the empty-body rule the
    # market feeds use — here the healthy body is empty by design, and this
    # route is polled every 3 min per open chart all evening, so a wrong
    # judgement would flood the ring with false failures every night.
    try:
        from app import vendor_meter as _vm
        _vm.record(isinstance(row, dict))
    except Exception:
        pass
    if not isinstance(row, dict):
        out = {"ticker": tk, "available": False, "values": [], "reason": "vendor_error"}
        _cache[tk] = (now, out)
        return out

    vals = []
    for v in (row.get("values") or []):
        if isinstance(v, dict) and v.get("price") is not None:
            vals.append({"t": v.get("timeStamp") or v.get("time"), "price": v.get("price")})
    cur = row.get("returnValue")
    net = row.get("netChange")
    prev = (cur - net) if (cur is not None and net is not None) else None
    out = {"ticker": tk, "available": bool(vals), "values": vals,
           "price": cur, "change": net, "pct": row.get("percentChange"), "prev": prev}
    _cache[tk] = (now, out)
    return out
