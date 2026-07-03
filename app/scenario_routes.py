"""
app/scenario_routes.py — saved DCF scenarios (persist your slider what-ifs).

Auth-scoped by user_key = f"u{user.id}" like watchlist/portfolio:

  GET    /api/scenarios?ticker=TCS   → this user's saved scenarios (optionally for one name)
  POST   /api/scenarios              → save/overwrite a named scenario (the assumptions dict)
  DELETE /api/scenarios/{id}         → delete one
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app import models
from app.auth import get_current_user

router = APIRouter(prefix="/api/scenarios", tags=["scenarios"])


class ScenarioUpsert(BaseModel):
    ticker: str
    name: str
    data: dict


def _row(r: models.SavedScenario) -> dict:
    return {"id": r.id, "ticker": r.ticker, "name": r.name, "data": r.data,
            "created_at": r.created_at.isoformat() if r.created_at else None}


@router.get("")
def list_scenarios(ticker: str | None = None,
                   user: models.User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    uk = f"u{user.id}"
    q = db.query(models.SavedScenario).filter_by(user_key=uk)
    if ticker:
        q = q.filter_by(ticker=ticker.upper())
    rows = q.order_by(models.SavedScenario.ticker, models.SavedScenario.name).all()
    return {"count": len(rows), "items": [_row(r) for r in rows]}


@router.post("")
def save_scenario(body: ScenarioUpsert,
                  user: models.User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    if not body.name.strip():
        raise HTTPException(400, "Scenario name required")
    uk, tk = f"u{user.id}", body.ticker.upper()
    row = (db.query(models.SavedScenario)
             .filter_by(user_key=uk, ticker=tk, name=body.name.strip()).first())
    if row:
        row.data = body.data                    # overwrite same-name scenario
    else:
        row = models.SavedScenario(user_key=uk, ticker=tk, name=body.name.strip(), data=body.data)
        db.add(row)
    db.commit()
    db.refresh(row)
    return _row(row)


@router.delete("/{scenario_id}")
def delete_scenario(scenario_id: int,
                    user: models.User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    uk = f"u{user.id}"
    row = db.query(models.SavedScenario).filter_by(id=scenario_id, user_key=uk).first()
    if row:
        db.delete(row)
        db.commit()
    return {"ok": True, "removed": bool(row)}
