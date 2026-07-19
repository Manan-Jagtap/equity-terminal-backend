"""
app/ownership_routes.py — cross-company institutional & MF ownership trends.

  GET /api/ownership → one row per Nifty name: promoter / FII / DII / mutual-fund /
                       public holding %, each with its QoQ delta, plus the combined
                       institutional (FII+DII) change. Sorted by institutional flow.

Reads stored insight data['ownership'] (populated by the ingester from the same
/stock payload it already fetches) so the page is instant.
"""
import time

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app import models

router = APIRouter(prefix="/api", tags=["ownership"])

# PERF-07: same 5-min in-process cache as /api/results (measured 1.1s/hit
# uncached, 374KB; data changes only on the daily ingest).
_OWNERSHIP_CACHE = {"ts": 0.0, "data": None}


@router.get("/ownership")
def ownership(db: Session = Depends(get_db)):
    if _OWNERSHIP_CACHE["data"] is not None and time.time() - _OWNERSHIP_CACHE["ts"] < 300:
        return _OWNERSHIP_CACHE["data"]
    insights = {r.company_id: r.data for r in db.query(models.CompanyInsight).all() if r.data}

    out = []
    for co in db.query(models.Company).all():
        own = (insights.get(co.id) or {}).get("ownership")
        if not own:
            continue
        out.append({
            "ticker": co.ticker, "name": co.name, "sector": co.sector, "type": co.type,
            "as_of": own.get("as_of"),
            "promoter": own.get("promoter"), "fii": own.get("fii"), "dii": own.get("dii"),
            "mf": own.get("mf"), "public": own.get("public"),
            "institutional": own.get("institutional"),
        })

    def _key(r):
        d = (r.get("institutional") or {}).get("delta")
        return d if d is not None else -999
    out.sort(key=_key, reverse=True)
    payload = {"count": len(out), "items": out}
    _OWNERSHIP_CACHE["data"], _OWNERSHIP_CACHE["ts"] = payload, time.time()
    return payload
