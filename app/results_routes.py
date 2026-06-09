"""
app/results_routes.py — cross-company earnings scoreboard.

  GET /api/results  → one row per Nifty name: latest reported quarter + sales /
                      PAT / EPS / OPM and YoY (from the stored results snapshot),
                      the latest FY EPS beat/miss vs estimate, plus rating/price.

Reads stored insight data (populated by the ingester's _results_snapshot +
forecasts) so the page is instant — no live per-company fan-out.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app import models
from app.results_logic import eps_surprise

router = APIRouter(prefix="/api", tags=["results"])


@router.get("/results")
def results(db: Session = Depends(get_db)):
    from app.ingest.indianapi_ingester import NIFTY_50
    insights = {r.company_id: r.data for r in db.query(models.CompanyInsight).all() if r.data}
    price_by = {m.company_id: m.price for m in db.query(models.MarketSnapshot).all()}
    val_by = {}
    try:
        val_by = {v.company_id: v for v in db.query(models.Valuation).all()}
    except Exception:
        db.rollback()

    out = []
    for co in db.query(models.Company).all():
        if (co.ticker or "").upper() not in NIFTY_50:
            continue
        d = insights.get(co.id) or {}
        res = d.get("results") or {}
        surprise = eps_surprise(d.get("forecasts"))
        if not res and not surprise:
            continue
        v = val_by.get(co.id)
        out.append({
            "ticker": co.ticker, "name": co.name, "sector": co.sector, "type": co.type,
            "price": price_by.get(co.id),
            "quarter": res.get("quarter"), "sales": res.get("sales"), "pat": res.get("pat"),
            "eps": res.get("eps"), "opm": res.get("opm"),
            "sales_yoy": res.get("sales_yoy"), "pat_yoy": res.get("pat_yoy"),
            "surprise": surprise,
            "rating": (v.analyst_rating if v else None),
            "verdict": (v.verdict if v else None),
        })

    # Newest report first (by EPS report date, then quarter label).
    def _key(r):
        s = r.get("surprise") or {}
        return (str(s.get("date") or ""), str(r.get("quarter") or ""))
    out.sort(key=_key, reverse=True)
    return {"count": len(out), "items": out}
