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
    insights = {r.company_id: r.data for r in db.query(models.CompanyInsight).all() if r.data}
    price_by = {m.company_id: m.price for m in db.query(models.MarketSnapshot).all()}
    val_by = {}
    try:
        val_by = {v.company_id: v for v in db.query(models.Valuation).all()}
    except Exception:
        db.rollback()

    out = []
    for co in db.query(models.Company).all():
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


@router.get("/results/upcoming")
def upcoming_results(db: Session = Depends(get_db)):
    """Board-meeting dates with a results agenda across the whole universe —
    the 'when do they report' calendar. Populated by the scheduler's weekly
    results-calendar sweep (stored on CompanyInsight.data['board_meetings']);
    past meetings older than 3 days are dropped."""
    import datetime as _dt
    import re as _re
    today = _dt.date.today()
    insights = {r.company_id: r.data for r in db.query(models.CompanyInsight).all() if r.data}
    out = []
    for co in db.query(models.Company).all():
        for m in (insights.get(co.id) or {}).get("board_meetings") or []:
            ds = str(m.get("date") or "")
            mm = _re.match(r"(\d{2})-(\d{2})-(\d{4})", ds)
            if not mm:
                continue
            d = _dt.date(int(mm.group(3)), int(mm.group(2)), int(mm.group(1)))
            if d < today - _dt.timedelta(days=3):
                continue
            agenda = str(m.get("agenda") or "")
            is_results = bool(_re.search(r"financial result|quarterly result|audited", agenda, _re.I))
            out.append({"ticker": co.ticker, "name": co.name, "sector": co.sector,
                        "date": d.isoformat(), "days_away": (d - today).days,
                        "results_meeting": is_results,
                        "agenda": agenda[:220]})
    # The exchange often files TWO notices per meeting (a terse "Quarterly
    # Results" plus the verbose text) — merge per (ticker, date), preferring
    # the terse agenda and OR-ing the results flag.
    merged: dict[tuple, dict] = {}
    for r in out:
        k = (r["ticker"], r["date"])
        cur = merged.get(k)
        if cur is None:
            merged[k] = r
        else:
            cur["results_meeting"] = cur["results_meeting"] or r["results_meeting"]
            if len(r["agenda"]) < len(cur["agenda"]):
                cur["agenda"] = r["agenda"]
    out = sorted(merged.values(), key=lambda r: (r["date"], r["ticker"]))
    return {"count": len(out), "items": out}
