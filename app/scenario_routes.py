"""
app/scenario_routes.py — saved DCF scenarios (persist your slider what-ifs).

Auth-scoped by user_key = f"u{user.id}" like watchlist/portfolio:

  GET    /api/scenarios?ticker=TCS   → this user's saved scenarios (optionally for one name)
  POST   /api/scenarios              → save/overwrite a named scenario (the assumptions dict)
  DELETE /api/scenarios/{id}         → delete one

Shareable links (the collaboration primitive CapIQ sells):

  POST   /api/scenarios/{id}/share   → owner mints an HMAC-signed share token
  GET    /api/scenarios/shared/{tok} → PUBLIC read-only view (ticker+name+data,
                                       never the owner). Stateless — no schema
                                       change; a link dies when its scenario is
                                       deleted, and tampered tokens don't parse.
"""
import hashlib
import hmac as _hmac

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app import models
from app.auth import get_current_user, SECRET, _b64url_encode, _b64url_decode

router = APIRouter(prefix="/api/scenarios", tags=["scenarios"])


def _share_token(scenario_id: int) -> str:
    payload = str(scenario_id).encode("utf-8")
    sig = _hmac.new(SECRET, b"scenario-share:" + payload, hashlib.sha256).hexdigest()[:32]
    return f"{_b64url_encode(payload)}.{sig}"


def parse_share_token(token: str) -> int | None:
    """Verify signature; return the scenario id or None. Constant-time compare."""
    try:
        body, sig = token.split(".", 1)
        payload = _b64url_decode(body)
        want = _hmac.new(SECRET, b"scenario-share:" + payload, hashlib.sha256).hexdigest()[:32]
        if not _hmac.compare_digest(sig, want):
            return None
        return int(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


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


@router.get("/shared/{token}")
def get_shared(token: str, db: Session = Depends(get_db)):
    """Public read-only view of a shared scenario. Returns ticker/name/data —
    deliberately not the owner. 404 for bad tokens and deleted scenarios alike
    (no oracle for probing which ids exist)."""
    sid = parse_share_token(token)
    row = db.query(models.SavedScenario).filter_by(id=sid).first() if sid else None
    if not row:
        raise HTTPException(404, "Scenario not found")
    return {"ticker": row.ticker, "name": row.name, "data": row.data,
            "created_at": row.created_at.isoformat() if row.created_at else None}


@router.post("/{scenario_id}/share")
def share_scenario(scenario_id: int,
                   user: models.User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    uk = f"u{user.id}"
    row = db.query(models.SavedScenario).filter_by(id=scenario_id, user_key=uk).first()
    if not row:
        raise HTTPException(404, "Scenario not found")
    return {"token": _share_token(row.id), "ticker": row.ticker, "name": row.name}


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
