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


@router.get("/coverage")
def coverage(db: Session = Depends(get_db), _admin: models.User = Depends(require_admin)):
    """Per-name, per-tab data-coverage matrix for the visible universe — which
    tab is empty for which company, and the summary counts. The fundamentals
    backfill targets exactly the names this flags."""
    from app.coverage import coverage_rows, summary
    from app.ingest.indianapi_ingester import VISIBLE_UNIVERSE
    rows = coverage_rows(db, VISIBLE_UNIVERSE)
    return {"summary": summary(rows),
            "gaps": [r for r in rows
                     if r["statements"] < 4 or not r["has_insight"]],
            "rows": rows}


@router.get("/dhan-totp")
def dhan_totp_check(_admin: models.User = Depends(require_admin)):
    """Owner-only TOTP sanity check: shows the code OUR server derives from
    DHAN_TOTP_SECRET right now, so the owner can hold it next to their
    authenticator app. Match → the stored secret is right (suspect PIN or an
    unfinished TOTP activation on web.dhan.co). Mismatch → re-copy the secret.
    Never returns the secret itself; a 30-second one-time code is worthless
    on its own."""
    import time
    from app.dhan import auth
    if not auth.enabled():
        return {"enabled": False,
                "message": "DHAN_CLIENT_ID / DHAN_PIN / DHAN_TOTP_SECRET not all set."}
    _, _, sec = auth._creds()
    now = time.time()
    return {
        "enabled": True,
        "server_code": auth.totp_now(sec),
        "seconds_left": int(30 - now % 30),
        "secret_len": len(sec),
        "how_to": "Open your authenticator's Dhan API entry NOW. If its code "
                  "differs from server_code, the secret saved on Railway is not "
                  "the one Dhan issued — re-copy it. If codes MATCH but token "
                  "minting still fails, the API TOTP setup on web.dhan.co was "
                  "not completed (it must be confirmed with a code) or the PIN "
                  "is wrong.",
    }
