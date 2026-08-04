"""
app/logo_routes.py — company-logo proxy.

  GET /api/logo/{ticker} → the company's logo image.

IndianAPI serves logos at /logo/{ticker_id}.png behind the API key, so a browser
<img> can't fetch them directly. This proxies the image (using the ticker_id we
already store on CompanyInsight), caches it in memory, and serves it with a long
cache header. 404s cleanly so the frontend can fall back to its neutral tile.
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
import time as _time
_CACHE: dict = {}   # ticker -> (bytes, content_type) | ("neg", retry_after_ts)
_NEG_TTL = 6 * 3600   # a missing ticker_id/logo can appear later — retry after 6h
_HEADERS = {"Cache-Control": "public, max-age=604800"}   # 7 days


@router.get("/logo/{ticker}")
def logo(ticker: str, db: Session = Depends(get_db)):
    t = ticker.upper()
    c = _CACHE.get(t)
    if c is not None:
        if c[0] == "neg":
            if _time.time() < c[1]:
                raise HTTPException(404, "no logo")
        else:
            return Response(content=c[0], media_type=c[1], headers=_HEADERS)

    co = db.query(models.Company).filter_by(ticker=t).first()
    if not co:
        raise HTTPException(404, f"unknown ticker {ticker}")
    row = db.query(models.CompanyInsight).filter_by(company_id=co.id).first()
    tid = (row.ticker_id if row else None)
    if not tid:
        _CACHE[t] = ("neg", _time.time() + _NEG_TTL)
        raise HTTPException(404, "no ticker_id for logo")

    # The production IndianAPI host dropped /logo/{id}; the vendor's own
    # payloads (peerCompanyList imageUrl) point at this CDN keyed by the same
    # ticker_id — the identical artwork the licensed feed serves elsewhere.
    for url in (f"https://www.livemint.com/lm-img/markets/logo/{tid}.png",
                f"{BASE}/logo/{tid}.png"):
        try:
            # SEND THE CREDENTIAL ONLY TO THE VENDOR HOST. This loop used to
            # attach the paid IndianAPI key to BOTH requests, so every logo
            # cache miss handed the key to www.livemint.com — an unrelated third
            # party that has no need for it and never asked. A public CDN image
            # needs no authentication at all.
            _vendor = url.startswith(BASE)
            hdrs = {"X-API-Key": KEY, "x-api-key": KEY} if _vendor else {}
            # ...and only a VENDOR call may tick the vendor meter. Counting the
            # CDN fetch inflated our own quota tally against a request IndianAPI
            # never saw, which is part of why the internal meter read far above
            # the vendor dashboard.
            if _vendor:
                from app import vendor_meter; vendor_meter.tick()  # FIX-07
            r = requests.get(url, headers=hdrs, timeout=15)
            ct = r.headers.get("content-type", "")
            if r.status_code == 200 and "image" in ct and r.content:
                _CACHE[t] = (r.content, ct or "image/png")
                return Response(content=r.content, media_type=ct or "image/png", headers=_HEADERS)
        except Exception:
            continue
    _CACHE[t] = ("neg", _time.time() + _NEG_TTL)
    raise HTTPException(404, "logo unavailable")
