"""
app/macro_routes.py — public India macro endpoints for the Economy dashboard.

  GET /api/macro                 → curated dashboard (sections + summary block)
  GET /api/macro/series/{slug}   → one series' full history (for a chart)
  GET /api/macro/catalog         → every available series (slug + coverage)

All read-only, no user data, all from primary official sources (RBI DBIE,
MoSPI, GSTN, NPCI, Grid India). Never a third-party proprietary monitor.
Cached in-process 30 min — the underlying data moves daily at most.
"""
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app import macro_data

router = APIRouter(prefix="/api/macro", tags=["macro"])

_cache: dict = {}
_TTL = 1800


@router.get("")
def macro_dashboard(db: Session = Depends(get_db)):
    hit = _cache.get("dash")
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    data = macro_data.dashboard(db)
    _cache["dash"] = (time.time(), data)
    return data


@router.get("/catalog")
def macro_catalog(db: Session = Depends(get_db)):
    return {"series": macro_data.catalog(db)}


@router.get("/series/{slug}")
def macro_series(slug: str, yoy: bool = False, db: Session = Depends(get_db)):
    """Full history for one series. yoy=true returns the year-on-year RATE
    (for index series like CPI/WPI/IIP), so a chart shows inflation as 3-6%,
    not the underlying 85→106 index level."""
    pts = macro_data.series(db, slug)
    if not pts:
        raise HTTPException(404, f"No macro series '{slug}'")
    cat = macro_data.catalog(db).get(slug, {})
    if yoy:
        yp = macro_data.yoy_series(pts)
        return {"slug": slug, "name": (cat.get("name", slug) + " — YoY %"),
                "freq": cat.get("freq"), "unit": "%",
                "points": [{"date": d, "value": v} for d, v in yp]}
    return {"slug": slug, "name": cat.get("name", slug), "freq": cat.get("freq"),
            "points": [{"date": d, "value": v} for d, v in pts]}


@router.get("/regulatory")
def regulatory_feed(db: Session = Depends(get_db)):
    """Recent RBI + SEBI regulatory items (official RSS feeds), keyword-tagged
    by the market surface they touch. Titles + links only — read the circular
    on the regulator's own site. Refreshed daily; fetched once on cold start."""
    from app import regulatory
    data = regulatory.load(db)
    if not (data.get("items")):
        try:
            regulatory.refresh(db)
            data = regulatory.load(db)
        except Exception:
            db.rollback()
    return data
