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
import hashlib
import re
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app import models, mailer
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
    consent: bool = False       # privacy-policy acceptance (required)


class LoginBody(BaseModel):
    email: str
    password: str


def _user_payload(user: models.User) -> dict:
    return {"id": user.id, "email": user.email, "name": user.name}


def _auth_response(user: models.User) -> dict:
    return {"token": create_token(user.id, user.email), "user": _user_payload(user)}


@router.post("/signup")
def signup(body: SignupBody, request: Request, db: Session = Depends(get_db)):
    from app.email_check import validate_email
    email = (body.email or "").strip().lower()
    name = (body.name or "").strip()
    if not _EMAIL_RE.match(email):
        raise HTTPException(400, "Invalid email address")
    email_problem = validate_email(email)     # syntax + disposable + DNS/MX
    if email_problem:
        raise HTTPException(400, email_problem)
    if not name:
        raise HTTPException(400, "Name is required")
    if len(body.password or "") < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    if not body.consent:
        raise HTTPException(400, "Please accept the Privacy Policy to create an account")
    if db.query(models.User).filter_by(email=email).first():
        raise HTTPException(400, "An account with this email already exists")

    # ── Email-ownership verification (when SMTP is configured) ──────────────
    # The account does NOT exist until the emailed 6-digit code is entered:
    # the pending signup (with a HASHED code) lives in kv_store, so a mistyped
    # or someone-else's address can never become a login. Without SMTP config
    # (dev/tests/CI) the flow falls through to immediate creation, unchanged.
    if mailer.configured():
        code = f"{secrets.randbelow(1000000):06d}"
        _put_pending(db, email, {
            "name": name,
            "password_hash": hash_password(body.password),
            "code_sha": hashlib.sha256(code.encode()).hexdigest(),
            "expires": time.time() + 30 * 60,
            "attempts": 0,
            "sent_at": time.time(),
        })
        try:
            mailer.send_email(
                email, "Your EquityVerdict verification code",
                f"Welcome to EquityVerdict.\n\nYour verification code is: {code}\n\n"
                "It expires in 30 minutes. If you didn't create this account, ignore this email.")
        except Exception:
            _del_pending(db, email)
            raise HTTPException(502, "Could not send the verification email — try again in a minute")
        _record_event(db, request, "signup_pending", email, None)
        return {"pending_verification": True, "email": email}

    return _create_user(db, request, email, name, hash_password(body.password))


def _create_user(db: Session, request: Request, email: str, name: str,
                 password_hash: str) -> dict:
    is_first_user = db.query(models.User).count() == 0

    user = models.User(email=email, name=name, password_hash=password_hash)
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


# ── Pending-signup store (kv_store; no schema change) ────────────────────────

def _pending_key(email: str) -> str:
    return f"pending_signup:{email}"


def _get_pending(db: Session, email: str) -> dict | None:
    row = db.query(models.KVStore).filter_by(key=_pending_key(email)).first()
    return row.value if row else None


def _put_pending(db: Session, email: str, value: dict) -> None:
    row = db.query(models.KVStore).filter_by(key=_pending_key(email)).first()
    if row:
        row.value = value
    else:
        db.add(models.KVStore(key=_pending_key(email), value=value))
    db.commit()


def _del_pending(db: Session, email: str) -> None:
    db.query(models.KVStore).filter_by(key=_pending_key(email)).delete()
    db.commit()


class VerifyBody(BaseModel):
    email: str
    code: str


@router.post("/verify")
def verify_signup(body: VerifyBody, request: Request, db: Session = Depends(get_db)):
    email = (body.email or "").strip().lower()
    p = _get_pending(db, email)
    if not p or time.time() > (p.get("expires") or 0):
        _del_pending(db, email)
        raise HTTPException(400, "Code expired or not found — sign up again")
    if (p.get("attempts") or 0) >= 5:
        _del_pending(db, email)
        _record_event(db, request, "verify_lockout", email, None)
        raise HTTPException(429, "Too many attempts — sign up again")
    given = hashlib.sha256((body.code or "").strip().encode()).hexdigest()
    if not secrets.compare_digest(given, p.get("code_sha") or ""):
        p["attempts"] = (p.get("attempts") or 0) + 1
        _put_pending(db, email, p)
        raise HTTPException(400, "Incorrect code")
    # Same duplicate guard as signup (covers a parallel signup racing this one).
    if db.query(models.User).filter_by(email=email).first():
        _del_pending(db, email)
        raise HTTPException(400, "An account with this email already exists")
    resp = _create_user(db, request, email, p.get("name") or "", p["password_hash"])
    _del_pending(db, email)
    return resp


class ResendBody(BaseModel):
    email: str


@router.post("/resend-code")
def resend_code(body: ResendBody, request: Request, db: Session = Depends(get_db)):
    """Re-email the verification code. Always 200 (no account enumeration);
    rate-limited to one send per 60s per address."""
    email = (body.email or "").strip().lower()
    p = _get_pending(db, email)
    if p and mailer.configured() and time.time() - (p.get("sent_at") or 0) >= 60 \
            and time.time() <= (p.get("expires") or 0):
        code = f"{secrets.randbelow(1000000):06d}"
        p["code_sha"] = hashlib.sha256(code.encode()).hexdigest()
        p["sent_at"] = time.time()
        p["attempts"] = 0
        _put_pending(db, email, p)
        try:
            mailer.send_email(
                email, "Your EquityVerdict verification code",
                f"Your new verification code is: {code}\n\nIt expires with your signup "
                "(30 minutes from the original request).")
        except Exception:
            pass
    return {"ok": True}


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


class DeleteAccountBody(BaseModel):
    confirm_email: str


@router.delete("/account")
def delete_account(body: DeleteAccountBody,
                   user: models.User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    """DPDP right-to-erasure: permanently removes the account and ALL personal
    data — watchlist, portfolio, saved scenarios/screens, and the auth-event
    ledger rows for this user. Requires the user to re-type their email (a
    stolen token alone shouldn't be able to silently destroy an account's
    data without knowing the address it belongs to)."""
    if (body.confirm_email or "").strip().lower() != (user.email or "").lower():
        raise HTTPException(400, "Email confirmation doesn't match this account")
    uk = f"u{user.id}"
    for model in (models.WatchlistItem, models.PortfolioHolding,
                  models.SavedScenario, models.SavedScreen):
        db.query(model).filter_by(user_key=uk).delete(synchronize_session=False)
    db.query(models.AuthEvent).filter(
        (models.AuthEvent.user_id == user.id) | (models.AuthEvent.email == user.email)
    ).delete(synchronize_session=False)
    db.query(models.User).filter_by(id=user.id).delete(synchronize_session=False)
    db.commit()
    return {"ok": True, "deleted": True}
