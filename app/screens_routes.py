"""
app/screens_routes.py — saved screener presets (name a filter set, reload it).

Auth-scoped by user_key = f"u{user.id}" like watchlist/portfolio/scenarios:

  GET    /api/screens        → this user's saved screens
  POST   /api/screens        → save/overwrite a named screen (the filter dict)
  DELETE /api/screens/{id}   → delete one
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app import models
from app.auth import get_current_user

router = APIRouter(prefix="/api/screens", tags=["screens"])


class ScreenUpsert(BaseModel):
    name: str
    data: dict


def _row(r: models.SavedScreen) -> dict:
    return {"id": r.id, "name": r.name, "data": r.data,
            "created_at": r.created_at.isoformat() if r.created_at else None}


@router.get("")
def list_screens(user: models.User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    uk = f"u{user.id}"
    rows = (db.query(models.SavedScreen).filter_by(user_key=uk)
              .order_by(models.SavedScreen.name).all())
    return {"count": len(rows), "items": [_row(r) for r in rows]}


@router.post("")
def save_screen(body: ScreenUpsert,
                user: models.User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    if not body.name.strip():
        raise HTTPException(400, "Screen name required")
    uk = f"u{user.id}"
    row = (db.query(models.SavedScreen)
             .filter_by(user_key=uk, name=body.name.strip()).first())
    if row:
        row.data = body.data                    # overwrite same-name screen
    else:
        row = models.SavedScreen(user_key=uk, name=body.name.strip(), data=body.data)
        db.add(row)
    db.commit()
    db.refresh(row)
    return _row(row)


@router.delete("/{screen_id}")
def delete_screen(screen_id: int,
                  user: models.User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    uk = f"u{user.id}"
    row = db.query(models.SavedScreen).filter_by(id=screen_id, user_key=uk).first()
    if row:
        db.delete(row)
        db.commit()
    return {"ok": True, "removed": bool(row)}
