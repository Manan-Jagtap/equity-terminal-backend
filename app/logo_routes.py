"""
app/logo_routes.py — company-logo proxy.

  GET /api/logo/{ticker} → the company's logo image.

IndianAPI serves logos at /logo/{ticker_id}.png behind the API key, so a browser
<img> can't fetch them directly. This proxies the image (using the ticker_id we
already store on CompanyInsight), caches it in memory, and serves it with a long
cache header. 404s cleanly so the frontend can fall back to a monogram.
"""
import os
import requests
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session
from app.database import get_db
from app import models

router = APIRouter(prefix="/api", tags=["logo"])

BASE = os.getenv("INDIANAPI_BASE", "https://stock.indianapi.in").rstrip("/")
KEY = os.getenv("INDIANAPI_KEY", "").strip()
_CACHE: dict = {}   # ticker -> (bytes, content_type) | None (negative cache)
_HEADERS = {"Cache-Control": "public, max-age=604800"}   # 7 days


@router.get("/logo/{ticker}")
def logo(ticker: str, db: Session = Depends(get_db)):
    t = ticker.upper()
    if t in _CACHE:
        c = _CACHE[t]
        if not c:
            raise HTTPException(404, "no logo")
        return Response(content=c[0], media_type=c[1], headers=_HEADERS)

    co = db.query(models.Company).filter_by(ticker=t).first()
    if not co:
        raise HTTPException(404, f"unknown ticker {ticker}")
    row = db.query(models.CompanyInsight).filter_by(company_id=co.id).first()
    tid = (row.ticker_id if row else None)
    if not tid:
        _CACHE[t] = None
        raise HTTPException(404, "no ticker_id for logo")

    for url in (f"{BASE}/logo/{tid}.png", f"{BASE}/logo/{tid}"):
        try:
            r = requests.get(url, headers={"X-API-Key": KEY, "x-api-key": KEY}, timeout=15)
            ct = r.headers.get("content-type", "")
            if r.status_code == 200 and "image" in ct and r.content:
                _CACHE[t] = (r.content, ct or "image/png")
                return Response(content=r.content, media_type=ct or "image/png", headers=_HEADERS)
        except Exception:
            continue
    _CACHE[t] = None
    raise HTTPException(404, "logo unavailable")
