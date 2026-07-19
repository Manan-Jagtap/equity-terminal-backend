"""Signup email-ownership verification: with SMTP configured no account may
exist until the emailed code is entered; without SMTP the legacy immediate
flow survives (dev/tests/CI)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_everify.db")
# Prod's 10/min auth cap would flake these rapid-fire flows (same convention
# as test_auth.py — must be set before app.main is first imported).
os.environ.setdefault("RATE_LIMIT_AUTH", "100")

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import Base, engine, SessionLocal
from app import models, mailer


@pytest.fixture()
def client():
    Base.metadata.create_all(engine)
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(engine)


def _smtp_on(monkeypatch, outbox):
    monkeypatch.setenv("EMAIL_SMTP_HOST", "smtp.test.local")
    monkeypatch.setenv("EMAIL_SMTP_USER", "admin@test.local")
    monkeypatch.setenv("EMAIL_SMTP_PASS", "x")
    monkeypatch.setattr(mailer, "send_email",
                        lambda to, subject, body: outbox.append((to, subject, body)))


def _code_from(body: str) -> str:
    import re
    return re.search(r"\b(\d{6})\b", body).group(1)


SIGNUP = {"email": "verify.me@gmail.com", "password": "longenough1",
          "name": "Verify Me", "consent": True}


def test_no_account_until_code_entered(client, monkeypatch):
    outbox = []
    _smtp_on(monkeypatch, outbox)
    r = client.post("/api/auth/signup", json=SIGNUP)
    assert r.status_code == 200 and r.json()["pending_verification"] is True
    assert len(outbox) == 1 and outbox[0][0] == SIGNUP["email"]

    s = SessionLocal()
    try:
        assert s.query(models.User).filter_by(email=SIGNUP["email"]).first() is None
    finally:
        s.close()

    # Login before verification must fail — the account doesn't exist.
    r = client.post("/api/auth/login", json={"email": SIGNUP["email"],
                                             "password": SIGNUP["password"]})
    assert r.status_code == 401

    code = _code_from(outbox[0][2])
    r = client.post("/api/auth/verify", json={"email": SIGNUP["email"], "code": code})
    assert r.status_code == 200 and r.json()["user"]["email"] == SIGNUP["email"]
    assert r.json()["token"]

    # Now login works, and the pending record is gone (code can't be replayed).
    r = client.post("/api/auth/login", json={"email": SIGNUP["email"],
                                             "password": SIGNUP["password"]})
    assert r.status_code == 200
    r = client.post("/api/auth/verify", json={"email": SIGNUP["email"], "code": code})
    assert r.status_code == 400


def test_wrong_code_locks_out_after_five(client, monkeypatch):
    outbox = []
    _smtp_on(monkeypatch, outbox)
    client.post("/api/auth/signup", json=SIGNUP)
    for _ in range(5):
        r = client.post("/api/auth/verify", json={"email": SIGNUP["email"], "code": "000001"})
        assert r.status_code == 400
    r = client.post("/api/auth/verify", json={"email": SIGNUP["email"], "code": "000001"})
    assert r.status_code == 429
    # Even the RIGHT code is dead after lockout — the pending signup is wiped.
    right = _code_from(outbox[0][2])
    r = client.post("/api/auth/verify", json={"email": SIGNUP["email"], "code": right})
    assert r.status_code == 400


def test_without_smtp_config_signup_is_immediate(client):
    assert not mailer.configured()
    r = client.post("/api/auth/signup", json=SIGNUP)
    assert r.status_code == 200 and "token" in r.json()


def test_signup_does_not_email_bomb_within_cooldown(client, monkeypatch):
    # SEC-03: a repeated signup for the same not-yet-registered address inside the
    # 60s cooldown must NOT send a second email (else signup is an email-bombing
    # lever against arbitrary third parties).
    outbox = []
    _smtp_on(monkeypatch, outbox)
    r1 = client.post("/api/auth/signup", json=SIGNUP)
    r2 = client.post("/api/auth/signup", json=SIGNUP)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r2.json()["pending_verification"] is True
    assert len(outbox) == 1          # second call acknowledged but sent nothing
