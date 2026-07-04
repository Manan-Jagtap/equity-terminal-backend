"""
app/quality_routes.py — data-health endpoints (the accuracy spine, surfaced).

  GET /api/quality/cross-check → second-source price cross-check over the
  visible universe (Dhan HistoricalPrice vs IndianAPI MarketSnapshot). Zero
  vendor calls — it reads what the ingest pipelines already stored. Cached
  briefly; the underlying data only moves on the daily jobs.
"""
import time

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.cross_check import cross_check_universe

router = APIRouter(prefix="/api/quality", tags=["quality"])

_CACHE: dict = {"t": 0.0, "data": None}
_TTL_S = 300


@router.get("/cross-check")
def cross_check(db: Session = Depends(get_db)):
    now = time.time()
    if _CACHE["data"] is not None and now - _CACHE["t"] < _TTL_S:
        return _CACHE["data"]
    from app.ingest.indianapi_ingester import VISIBLE_UNIVERSE
    data = cross_check_universe(db, VISIBLE_UNIVERSE)
    _CACHE.update(t=now, data=data)
    return data
