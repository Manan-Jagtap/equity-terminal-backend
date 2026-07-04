"""
app/auth_routes.py — signup / login / me.

  POST /api/auth/signup  {email, password, name?} → {"token", "user"}
                          400 on duplicate email, invalid email, or pw < 8 chars.
                          The FIRST EVER user adopts the legacy single-tenant
                          rows: watchlist_items / portfolio_holdings written
                          under user_key='default' become user_key=f"u{id}".
  POST /api/auth/login   {email, password}        → {"token", "user"} or 401.
  GET  /api/auth/me      (Bearer token)           → {"user": ...}

Response contract (do not change — frontend is built against it):
  {"token": "<opaque>", "user": {"id": int, "email": str, "name": str|None}}
"""
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app import models
from app.auth import create_token, get_current_user, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _record_event(db: Session, request: Request, event: str,
                  email: str, user_id: int | None):
    """Best-effort auth audit row — a logging failure must never block auth."""
    try:
        fwd = request.headers.get("x-forwarded-for", "")
        ip = (fwd.split(",")[0].strip() if fwd
              else (request.client.host if request.client else None))
        db.add(models.AuthEvent(
            user_id=user_id, email=(email or "")[:255], event=event,
            ip=(ip or "")[:64] or None,
            user_agent=(request.headers.get("user-agent") or "")[:256] or None))
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SignupBody(BaseModel):
    email: str
    password: str
    name: str | None = None


class LoginBody(BaseModel):
    email: str
    password: str


def _user_payload(user: models.User) -> dict:
    return {"id": user.id, "email": user.email, "name": user.name}


def _auth_response(user: models.User) -> dict:
    return {"token": create_token(user.id, user.email), "user": _user_payload(user)}


@router.post("/signup")
def signup(body: SignupBody, request: Request, db: Session = Depends(get_db)):
    email = (body.email or "").strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(400, "Invalid email address")
    if len(body.password or "") < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    if db.query(models.User).filter_by(email=email).first():
        raise HTTPException(400, "An account with this email already exists")

    is_first_user = db.query(models.User).count() == 0

    user = models.User(email=email, name=(body.name or None),
                       password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    db.refresh(user)

    if is_first_user:
        # Adopt the legacy single-tenant rows written before login existed.
        uk = f"u{user.id}"
        db.query(models.WatchlistItem).filter_by(user_key="default") \
          .update({"user_key": uk}, synchronize_session=False)
        db.query(models.PortfolioHolding).filter_by(user_key="default") \
          .update({"user_key": uk}, synchronize_session=False)
        db.commit()

    _record_event(db, request, "signup", email, user.id)
    return _auth_response(user)


@router.post("/login")
def login(body: LoginBody, request: Request, db: Session = Depends(get_db)):
    email = (body.email or "").strip().lower()
    user = db.query(models.User).filter_by(email=email).first()
    if not user or not verify_password(body.password or "", user.password_hash):
        _record_event(db, request, "login_failed", email, user.id if user else None)
        raise HTTPException(401, "Invalid email or password")
    _record_event(db, request, "login", email, user.id)
    return _auth_response(user)


@router.get("/me")
def me(user: models.User = Depends(get_current_user)):
    return {"user": _user_payload(user)}
