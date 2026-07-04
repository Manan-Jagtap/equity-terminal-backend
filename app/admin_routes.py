"""
app/admin_routes.py — owner-only visibility into who is signing up and coming
back. Gated by ADMIN_EMAILS (comma-separated, case-insensitive): the caller
must be authenticated AND their email must be on the list. 403 otherwise —
including when the env var is unset (fail closed, never open).

  GET /api/admin/users → [{id, email, name, created_at, last_login,
                           login_count, last_ip}] newest signup first
  GET /api/admin/auth-events?limit=100 → recent raw events (incl. failures)
"""
import os

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app import models
from app.auth import get_current_user

router = APIRouter(prefix="/api/admin", tags=["admin"])


def require_admin(user: models.User = Depends(get_current_user)) -> models.User:
    allowed = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()}
    if not allowed or (user.email or "").lower() not in allowed:
        raise HTTPException(403, "Admin access required")
    return user


@router.get("/users")
def list_users(user: models.User = Depends(require_admin),
               db: Session = Depends(get_db)):
    logins = dict(db.query(models.AuthEvent.user_id, func.count())
                    .filter(models.AuthEvent.event == "login")
                    .group_by(models.AuthEvent.user_id).all())
    last_seen = dict(db.query(models.AuthEvent.user_id, func.max(models.AuthEvent.created_at))
                       .filter(models.AuthEvent.event.in_(("login", "signup")))
                       .group_by(models.AuthEvent.user_id).all())
    last_ip = {}
    for ev in (db.query(models.AuthEvent)
                 .filter(models.AuthEvent.user_id.isnot(None))
                 .order_by(models.AuthEvent.created_at).all()):
        if ev.ip:
            last_ip[ev.user_id] = ev.ip
    users = db.query(models.User).order_by(models.User.created_at.desc()).all()
    return {"count": len(users), "users": [{
        "id": u.id, "email": u.email, "name": u.name,
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "last_login": last_seen[u.id].isoformat() if last_seen.get(u.id) else None,
        "login_count": logins.get(u.id, 0),
        "last_ip": last_ip.get(u.id),
    } for u in users]}


@router.get("/auth-events")
def list_auth_events(limit: int = 100,
                     user: models.User = Depends(require_admin),
                     db: Session = Depends(get_db)):
    rows = (db.query(models.AuthEvent)
              .order_by(models.AuthEvent.created_at.desc())
              .limit(max(1, min(limit, 500))).all())
    return {"count": len(rows), "events": [{
        "email": r.email, "event": r.event, "ip": r.ip,
        "user_agent": r.user_agent,
        "at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]}
