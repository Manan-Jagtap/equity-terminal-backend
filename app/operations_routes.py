"""
app/operations_routes.py — cross-company operating-efficiency screen.

  GET /api/operations → one row per Nifty name: ROCE (+3y trend), ROE, debtor /
                        inventory / payable days, cash-conversion cycle (+3y
                        trend), working-capital days, asset turnover.

Reads the Screener-style `ratios` already stored on each CompanyInsight, so no
re-ingest is needed.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app import models
from app.operations_logic import operational_snapshot

router = APIRouter(prefix="/api", tags=["operations"])


@router.get("/operations")
def operations(db: Session = Depends(get_db)):
    from app.ingest.indianapi_ingester import NIFTY_50
    insights = {r.company_id: r.data for r in db.query(models.CompanyInsight).all() if r.data}
    val_by = {}
    try:
        val_by = {v.company_id: v for v in db.query(models.Valuation).all()}
    except Exception:
        db.rollback()

    out = []
    for co in db.query(models.Company).all():
        if (co.ticker or "").upper() not in NIFTY_50:
            continue
        snap = operational_snapshot((insights.get(co.id) or {}).get("ratios"))
        if not snap:
            continue
        v = val_by.get(co.id)
        out.append({
            "ticker": co.ticker, "name": co.name, "sector": co.sector, "type": co.type,
            "verdict": (v.verdict if v else None),
            **snap,
        })

    out.sort(key=lambda r: (r.get("roce") if r.get("roce") is not None else -999), reverse=True)
    return {"count": len(out), "items": out}
