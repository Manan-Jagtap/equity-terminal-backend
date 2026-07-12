"""
app/dhan/auth.py — self-renewing Dhan access tokens.

Dhan tokens live 24 hours (SEBI guideline). The July 2026 outage happened
because the manually-pasted DHAN_ACCESS_TOKEN silently expired and every feed
(live prices, history top-ups, options) died for a week. With TOTP enabled on
the account, Dhan's official generateAccessToken endpoint mints a fresh token
from (client id, PIN, TOTP code) — so the platform renews itself.

Credentials come ONLY from environment variables the owner sets on Railway
(never through chat/code): DHAN_CLIENT_ID, DHAN_PIN, DHAN_TOTP_SECRET.
This module never calls trading endpoints — data auth only.

The active token is shared through the kv_store table so the backend and the
scheduler use ONE token instead of racing to mint two; whichever process finds
it stale regenerates and writes back. Precedence in client.access_token():
auto-managed token first (self-healing), env DHAN_ACCESS_TOKEN as fallback.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import struct
import threading
import time

import httpx

_GEN_URL = "https://auth.dhan.co/app/generateAccessToken"

_lock = threading.Lock()
_mem: dict = {"token": "", "exp": 0.0, "next_try": 0.0}

# Regenerate when this close to expiry — long enough that a token handed to a
# long request doesn't die mid-flight, short enough to stay fresh.
_EARLY = 30 * 60

# After a FAILED mint, back off before trying again. Without this, the 15s
# live-price poll re-attempted generateAccessToken on every request and
# tripped Dhan's "Too many attempts" lockout within minutes.
_FAIL_COOLDOWN = 10 * 60


def _creds() -> tuple[str, str, str]:
    return (os.getenv("DHAN_CLIENT_ID", "").strip(),
            os.getenv("DHAN_PIN", "").strip(),
            os.getenv("DHAN_TOTP_SECRET", "").strip().replace(" ", ""))


def enabled() -> bool:
    cid, pin, sec = _creds()
    return bool(cid and pin and sec)


def totp_now(secret: str, at: float | None = None, digits: int = 6, step: int = 30) -> str:
    """RFC 6238 TOTP (SHA-1), stdlib only — no new dependency for one HMAC."""
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    counter = int((at if at is not None else time.time()) // step)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def _jwt_exp(token: str) -> float:
    """Expiry from the token's own exp claim (matches by construction)."""
    try:
        body = token.split(".")[1]
        body += "=" * (-len(body) % 4)
        return float(json.loads(base64.urlsafe_b64decode(body)).get("exp") or 0)
    except Exception:
        return 0.0


def _db():
    from app.database import SessionLocal
    return SessionLocal()


def _read_store() -> tuple[str, float]:
    try:
        from app import models
        s = _db()
        try:
            row = s.query(models.KVStore).filter_by(key="dhan_token").first()
            if row and isinstance(row.value, dict):
                return str(row.value.get("token") or ""), float(row.value.get("exp") or 0)
        finally:
            s.close()
    except Exception:
        pass
    return "", 0.0


def _write_store(token: str, exp: float) -> None:
    try:
        from app import models
        s = _db()
        try:
            row = s.query(models.KVStore).filter_by(key="dhan_token").first()
            if row is None:
                row = models.KVStore(key="dhan_token", value={})
                s.add(row)
            row.value = {"token": token, "exp": exp}
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(row, "value")
            s.commit()
        finally:
            s.close()
    except Exception:
        pass


def _generate() -> tuple[str, float]:
    cid, pin, sec = _creds()
    try:
        r = httpx.post(_GEN_URL,
                       params={"dhanClientId": cid, "pin": pin,
                               "totp": totp_now(sec)},
                       timeout=20)
        r.raise_for_status()
        tok = str((r.json() or {}).get("accessToken") or "")
        if tok:
            exp = _jwt_exp(tok) or (time.time() + 23 * 3600)
            return tok, exp
    except Exception:
        pass
    return "", 0.0


def auto_token() -> str:
    """A currently-valid auto-managed token, or "" when auto-auth is not
    configured / generation failed (caller falls back to the env token)."""
    if not enabled():
        return ""
    now = time.time()
    with _lock:
        if _mem["token"] and _mem["exp"] - now > _EARLY:
            return _mem["token"]
    tok, exp = _read_store()
    if tok and exp - now > _EARLY:
        with _lock:
            _mem.update(token=tok, exp=exp)
        return tok
    with _lock:
        cooling = now < _mem["next_try"]
    if not cooling:
        new_tok, new_exp = _generate()
        if new_tok:
            _write_store(new_tok, new_exp)
            with _lock:
                _mem.update(token=new_tok, exp=new_exp, next_try=0.0)
            return new_tok
        with _lock:
            _mem["next_try"] = now + _FAIL_COOLDOWN
    # Generation failed or cooling down — serve whatever is still alive.
    if tok and exp > now:
        with _lock:
            _mem.update(token=tok, exp=exp)
        return tok
    return ""


def invalidate(bad_token: str | None = None) -> None:
    """Called on a 401: drop the memory cache; if the shared store still holds
    the token that just failed, force the next auto_token() to regenerate."""
    with _lock:
        _mem.update(token="", exp=0.0)
    if bad_token:
        tok, _ = _read_store()
        if tok == bad_token:
            _write_store("", 0.0)
