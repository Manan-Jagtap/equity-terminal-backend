"""
app/watchlist_routes.py — saved-names watchlist + valuation alerts.

Endpoints (all scoped by a `user` key — defaults to "default" until login lands;
the frontend will pass the real key as ?user= or an X-User-Key header):

  GET    /api/watchlist            → watched names enriched with live verdict /
                                      intrinsic / MoS / price / 1-day move, plus
                                      the alerts each one currently triggers.
  POST   /api/watchlist            → add or update a watched name + its alert config.
  DELETE /api/watchlist/{ticker}    → stop watching a name.

Alert logic lives in app.watchlist_alerts (DB-free, unit-tested).
"""
from fastapi import APIRouter, Depends, HTTPException, Header, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import get_db
from app import models
from app.watchlist_alerts import compute_alerts

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])

_NORMALIZE_VERDICT = {"ACCUMULATE": "ACCUMULATE", "BUY": "BUY", "HOLD": "HOLD",
                      "REDUCE": "REDUCE", "AVOID": "AVOID", "LOW CONF": "LOW CONF",
                      "NO DATA": "NO DATA"}


def _user(user: str | None, x_user_key: str | None) -> str:
    return (user or x_user_key or "default").strip() or "default"


class WatchUpsert(BaseModel):
    ticker: str
    target_price: float | None = None
    mos_threshold: float | None = None
    move_threshold: float | None = None
    alert_verdict: bool | None = None
    alert_mos: bool | None = None
    alert_target: bool | None = None
    alert_move: bool | None = None
    note: str | None = None


def _day_move(db: Session, company_id: int):
    """1-day return from the last two stored daily closes, or None."""
    rows = (db.query(models.HistoricalPrice)
              .filter_by(company_id=company_id)
              .order_by(models.HistoricalPrice.date.desc())
              .limit(2).all())
    if len(rows) == 2 and rows[1].close:
        try:
            return rows[0].close / rows[1].close - 1.0
        except (TypeError, ZeroDivisionError):
            return None
    return None


def _enrich(db: Session, item: models.WatchlistItem):
    co = item.company
    price = (co.market.price if co.market else None)
    v = None
    try:
        v = db.query(models.Valuation).filter_by(company_id=co.id).first()
    except Exception:
        db.rollback()
    verdict = _NORMALIZE_VERDICT.get((v.verdict if v else None), (v.verdict if v else None))
    mos = v.mos if v else None
    move = _day_move(db, co.id)

    cfg = {"alert_verdict": bool(item.alert_verdict), "alert_mos": bool(item.alert_mos),
           "alert_target": bool(item.alert_target), "alert_move": bool(item.alert_move),
           "mos_threshold": item.mos_threshold, "move_threshold": item.move_threshold,
           "target_price": item.target_price, "last_verdict": item.last_verdict}
    cur = {"verdict": verdict, "mos": mos, "price": price, "day_move": move}
    alerts = compute_alerts(cfg, cur)

    # Persist transition state so the next read can detect verdict/price changes.
    item.last_verdict = verdict
    item.last_price = price

    return {
        "ticker": co.ticker, "name": co.name, "sector": co.sector, "type": co.type,
        "price": price, "day_move": move,
        "verdict": verdict, "intrinsic": (v.intrinsic if v else None), "mos": mos,
        "composite": (v.composite if v else None), "confidence": (v.confidence if v else None),
        "analyst_target": (v.analyst_target if v else None),
        "analyst_upside": (v.analyst_upside if v else None),
        "pe": (v.pe if v else None), "pb": (v.pb if v else None), "roe": (v.roe if v else None),
        # config
        "target_price": item.target_price, "mos_threshold": item.mos_threshold,
        "move_threshold": item.move_threshold, "note": item.note,
        "alert_verdict": bool(item.alert_verdict), "alert_mos": bool(item.alert_mos),
        "alert_target": bool(item.alert_target), "alert_move": bool(item.alert_move),
        "added_at": item.added_at.isoformat() if item.added_at else None,
        # alerts
        "alerts": alerts, "triggered": len(alerts) > 0,
    }


@router.get("")
def list_watchlist(user: str | None = Query(None), x_user_key: str | None = Header(None),
                   db: Session = Depends(get_db)):
    uk = _user(user, x_user_key)
    items = (db.query(models.WatchlistItem)
               .filter_by(user_key=uk)
               .join(models.Company).order_by(models.Company.ticker).all())
    out = [_enrich(db, it) for it in items]
    db.commit()   # persist refreshed last_verdict/last_price
    # Most-actionable first: triggered names on top, then by margin of safety.
    out.sort(key=lambda r: (r["triggered"], r["mos"] if r["mos"] is not None else -9), reverse=True)
    return {"user": uk, "count": len(out),
            "triggered": sum(1 for r in out if r["triggered"]), "items": out}


@router.post("")
def upsert_watchlist(body: WatchUpsert, user: str | None = Query(None),
                     x_user_key: str | None = Header(None), db: Session = Depends(get_db)):
    uk = _user(user, x_user_key)
    co = db.query(models.Company).filter_by(ticker=body.ticker.upper()).first()
    if not co:
        raise HTTPException(404, f"Unknown ticker {body.ticker}")
    item = (db.query(models.WatchlistItem)
              .filter_by(user_key=uk, company_id=co.id).first())
    if not item:
        item = models.WatchlistItem(user_key=uk, company_id=co.id)
        db.add(item)
    # Only overwrite fields that were explicitly provided.
    for field in ("target_price", "mos_threshold", "move_threshold", "note"):
        val = getattr(body, field)
        if val is not None:
            setattr(item, field, val)
    for field in ("alert_verdict", "alert_mos", "alert_target", "alert_move"):
        val = getattr(body, field)
        if val is not None:
            setattr(item, field, 1 if val else 0)
    db.commit()
    db.refresh(item)
    result = _enrich(db, item)
    db.commit()   # persist the refreshed transition state from _enrich
    return result


@router.delete("/{ticker}")
def delete_watchlist(ticker: str, user: str | None = Query(None),
                     x_user_key: str | None = Header(None), db: Session = Depends(get_db)):
    uk = _user(user, x_user_key)
    co = db.query(models.Company).filter_by(ticker=ticker.upper()).first()
    if not co:
        raise HTTPException(404, f"Unknown ticker {ticker}")
    item = db.query(models.WatchlistItem).filter_by(user_key=uk, company_id=co.id).first()
    if item:
        db.delete(item)
        db.commit()
    return {"ok": True, "ticker": co.ticker, "removed": bool(item)}
