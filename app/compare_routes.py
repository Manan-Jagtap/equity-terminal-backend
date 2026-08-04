from app.valuation_public import is_suppressed_row, FAIR_VALUE_NM
"""
app/compare_routes.py — side-by-side company comparison.

  GET /api/compare?tickers=TCS,INFY,WIPRO

Assembles a normalized row per name from the precomputed Valuation table (verdict
/ intrinsic / MoS / multiples / analyst) plus the live forensic report (quality
grade, Altman Z'', Beneish M, Piotroski F). Unknown tickers are skipped. The
frontend handles best-in-column highlighting; here we just return clean values.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app import models
from app.forensics import forensic_report

router = APIRouter(prefix="/api", tags=["compare"])


def _statements(db: Session, company_id: int) -> dict:
    rows = (db.query(models.HistoricalFinancial)
              .filter_by(company_id=company_id)
              .order_by(models.HistoricalFinancial.fiscal_year).all())
    s: dict = {}
    for r in rows:
        if r.value is None:
            continue
        s.setdefault(int(r.fiscal_year), {}).setdefault(r.statement_type, {})[r.line_item] = r.value
    return s


@router.get("/compare")
def compare(tickers: str = Query(..., description="comma-separated, e.g. TCS,INFY,WIPRO"),
            db: Session = Depends(get_db)):
    names, seen = [], set()
    for t in (tickers or "").split(","):
        u = t.strip().upper()
        if u and u not in seen:
            seen.add(u)
            names.append(u)
    names = names[:6]   # cap

    items = []
    for t in names:
        co = db.query(models.Company).filter_by(ticker=t).first()
        if not co:
            continue
        price = (co.market.price if co.market else None)
        v = None
        try:
            v = db.query(models.Valuation).filter_by(company_id=co.id).first()
        except Exception:
            db.rollback()

        fr = forensic_report(_statements(db, co.id), {"type": co.type})
        m = fr.get("metrics", {})
        g = lambda key: (m.get(key) or {}).get("value")

        _sup = is_suppressed_row(v)
        items.append({
            "ticker": co.ticker, "name": co.name, "sector": co.sector, "type": co.type,
            "price": price,
            "market_cap": (price * co.shares_outstanding) if (price and co.shares_outstanding) else None,
            # Model valuation — DAT-13b/DAT-15: `v` is the STORED row and keeps
            # the raw figure by design, so the gate must be asked here too.
            # Compare puts names side by side, which is exactly where a withheld
            # value masquerading as a real one does the most damage.
            "intrinsic": (None if _sup else (v.intrinsic if v else None)),
            "mos": (None if _sup else (v.mos if v else None)),
            "value_suppressed": _sup or None,
            "fair_value_note": FAIR_VALUE_NM if _sup else None,
            "verdict": (v.verdict if v else None), "composite": (v.composite if v else None),
            "confidence": (v.confidence if v else None), "method": (v.method if v else None),
            # Multiples
            "pe": (v.pe if v else None), "pb": (v.pb if v else None), "roe": (v.roe if v else None),
            # Analyst consensus (separate)
            "analyst_target": (v.analyst_target if v else None),
            "analyst_upside": (v.analyst_upside if v else None),
            "analyst_rating": (v.analyst_rating if v else None),
            # Forensics
            "forensic_grade": fr.get("grade"), "forensic_composite": fr.get("composite"),
            "altman_z": g("altman_z"), "beneish_m": g("beneish_m"),
            "piotroski": (m.get("piotroski") or {}).get("normalized"),
            "forensic_flags": len(fr.get("flags", [])),
        })

    return {"count": len(items), "items": items}
