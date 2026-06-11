"""Integration tests for signup/login/me + the auth wall on personal data.

NOTE: app.main imports app.models → app.database, which binds the engine to
DATABASE_URL at import time. pytest imports test files alphabetically so this
file runs FIRST — it must (a) set the same test DB the other suites use BEFORE
importing any app module, and (b) start from a clean file so the
first-ever-user adoption test is deterministic (see tests/test_backtest.py)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

_TEST_DB = "/tmp/_pytest_terminal.db"
if os.path.exists(_TEST_DB):
    os.remove(_TEST_DB)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TEST_DB}")
# The auth rate limit (10/min) is deliberately tight for prod; raise it for the
# suite so the handful of auth calls across tests can't trip a flaky 429.
os.environ.setdefault("RATE_LIMIT_AUTH", "100")

from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app import models

client = TestClient(app)

PW = "correct-horse-9"


def _signup(email, pw=PW, name=None):
    return client.post("/api/auth/signup",
                       json={"email": email, "password": pw, "name": name})


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_first_user_adopts_legacy_default_rows():
    # Pre-seed single-tenant rows the way the app wrote them before login.
    db = SessionLocal()
    co = models.Company(ticker="ADOPTCO", name="Adopt Co", type="nonfinancial",
                        sector="IT", shares_outstanding=100.0)
    db.add(co)
    db.commit()
    db.refresh(co)
    db.add(models.WatchlistItem(user_key="default", company_id=co.id))
    db.add(models.PortfolioHolding(user_key="default", company_id=co.id,
                                   qty=10.0, avg_cost=5.0))
    db.commit()
    db.close()

    r = _signup("first@example.com", name="First User")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"token", "user"}
    uid, token = body["user"]["id"], body["token"]

    # Legacy rows were re-keyed to the first user.
    db = SessionLocal()
    assert db.query(models.WatchlistItem).filter_by(user_key="default").count() == 0
    assert db.query(models.WatchlistItem).filter_by(user_key=f"u{uid}").count() == 1
    assert db.query(models.PortfolioHolding).filter_by(user_key="default").count() == 0
    assert db.query(models.PortfolioHolding).filter_by(user_key=f"u{uid}").count() == 1
    db.close()

    # ...and are visible through the authenticated endpoints.
    r = client.get("/api/watchlist", headers=_auth(token))
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 1 and data["items"][0]["ticker"] == "ADOPTCO"

    r = client.get("/api/portfolio", headers=_auth(token))
    assert r.status_code == 200
    assert [i["ticker"] for i in r.json()["items"]] == ["ADOPTCO"]


def test_signup_login_me_roundtrip():
    r = _signup("Round.Trip@Example.COM", name="Rounder")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"token", "user"}
    assert set(body["user"]) == {"id", "email", "name"}
    assert body["user"]["email"] == "round.trip@example.com"   # lowercased
    assert body["user"]["name"] == "Rounder"

    r = client.post("/api/auth/login",
                    json={"email": "round.trip@example.com", "password": PW})
    assert r.status_code == 200
    login = r.json()
    assert set(login) == {"token", "user"}
    assert login["user"]["id"] == body["user"]["id"]

    r = client.get("/api/auth/me", headers=_auth(login["token"]))
    assert r.status_code == 200
    assert r.json() == {"user": {"id": body["user"]["id"],
                                 "email": "round.trip@example.com",
                                 "name": "Rounder"}}
    # Hardening headers ride on every response.
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


def test_wrong_password_is_401():
    r = client.post("/api/auth/login",
                    json={"email": "round.trip@example.com", "password": "wrong-pass-1"})
    assert r.status_code == 401


def test_signup_validation_400s():
    assert _signup("round.trip@example.com").status_code == 400      # duplicate
    assert _signup("short@example.com", pw="short").status_code == 400  # < 8 chars
    assert _signup("not-an-email", pw=PW).status_code == 400         # bad email


def test_watchlist_requires_auth():
    assert client.get("/api/watchlist").status_code == 401
    assert client.get("/api/watchlist",
                      headers=_auth("garbage.token")).status_code == 401
    assert client.get("/api/portfolio").status_code == 401

    r = _signup("watcher@example.com")
    assert r.status_code == 200
    token = r.json()["token"]
    r = client.get("/api/watchlist", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["count"] == 0   # new user sees their own (empty) list
