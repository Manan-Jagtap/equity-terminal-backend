"""
app/auth.py — stdlib-only authentication primitives + FastAPI dependencies.

No third-party crypto: passwords are PBKDF2-HMAC-SHA256 (260k iterations,
16-byte random salt) and session tokens are HMAC-SHA256-signed JSON payloads
(base64url(payload) + "." + hex signature) with an embedded expiry.

SECRET comes from the AUTH_SECRET env var; when unset we fall back to a
process-local os.urandom key, which means every restart invalidates all
outstanding tokens (logged loudly so it isn't a silent prod foot-gun).
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import time

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from .database import get_db

logger = logging.getLogger("app.auth")

_PBKDF2_ITERATIONS = 260_000

_env_secret = os.getenv("AUTH_SECRET")
if _env_secret:
    SECRET = _env_secret.encode("utf-8")
else:
    SECRET = os.urandom(32)
    logger.warning(
        "AUTH_SECRET is not set — using a random per-process secret. "
        "All sessions/tokens will be invalidated whenever the server restarts. "
        "Set AUTH_SECRET in the environment for stable sessions."
    )


# ── Passwords ────────────────────────────────────────────────────────────────
def hash_password(pw: str) -> str:
    """PBKDF2-HMAC-SHA256 → 'pbkdf2$<iterations>$<salt_hex>$<hash_hex>'."""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2${_PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    """Constant-time check of `pw` against a hash_password() string."""
    try:
        scheme, iters_s, salt_hex, hash_hex = stored.split("$")
        if scheme != "pbkdf2":
            return False
        iterations = int(iters_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(dk, expected)


# ── Tokens ───────────────────────────────────────────────────────────────────
def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def create_token(user_id: int, email: str, days: int = 30) -> str:
    """base64url(JSON payload incl. exp) + '.' + hex HMAC-SHA256(payload)."""
    payload = json.dumps(
        {"uid": user_id, "email": email,
         "exp": int(time.time()) + days * 86400},
        separators=(",", ":"),
    ).encode("utf-8")
    sig = hmac.new(SECRET, payload, hashlib.sha256).hexdigest()
    return f"{_b64url_encode(payload)}.{sig}"


def parse_token(token: str) -> dict | None:
    """Validate signature + expiry; return the payload dict or None."""
    try:
        payload_b64, sig_hex = token.split(".", 1)
        payload = _b64url_decode(payload_b64)
    except (ValueError, TypeError):
        return None
    expected = hmac.new(SECRET, payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig_hex):
        return None
    try:
        data = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        return None
    exp = data.get("exp")
    if not isinstance(exp, (int, float)) or time.time() >= exp:
        return None
    return data


# ── FastAPI dependencies ─────────────────────────────────────────────────────
def _user_from_authorization(authorization: str | None, db: Session):
    from . import models
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        return None
    payload = parse_token(parts[1].strip())
    if payload is None:
        return None
    uid = payload.get("uid")
    if uid is None:
        return None
    return db.query(models.User).filter_by(id=uid).first()


def get_current_user(authorization: str | None = Header(None),
                     db: Session = Depends(get_db)):
    """Require a valid 'Authorization: Bearer <token>' header → User row.

    Raises 401 when the header is missing, malformed, expired, tampered with,
    or references a user that no longer exists."""
    user = _user_from_authorization(authorization, db)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated",
                            headers={"WWW-Authenticate": "Bearer"})
    return user


def get_optional_user(authorization: str | None = Header(None),
                      db: Session = Depends(get_db)):
    """Like get_current_user but returns None instead of raising 401."""
    return _user_from_authorization(authorization, db)
