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
    # Feed status rides along: an expired Dhan token is the single most likely
    # cause of mass staleness (it froze the pipeline for 8 days once, with the
    # alarm firing but nothing naming the culprit). Name it, loudly.
    try:
        import base64 as _b64
        import json as _json
        import datetime as _dt
        from app.dhan import client as _dhan
        tok = _dhan.access_token()
        body = tok.split(".")[1]
        body += "=" * (-len(body) % 4)
        exp = _json.loads(_b64.urlsafe_b64decode(body)).get("exp")
        if exp:
            exp_dt = _dt.datetime.fromtimestamp(int(exp), _dt.timezone.utc)
            data["feed"] = {
                "token_expires_utc": exp_dt.isoformat(),
                "token_expired": exp_dt < _dt.datetime.now(_dt.timezone.utc),
            }
    except Exception:
        pass
    _CACHE.update(t=now, data=data)
    return data
