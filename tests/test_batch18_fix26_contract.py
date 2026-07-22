"""Batch 18 — FIX-26 (TEST-04): the FE↔BE response contract. Asserts the key-set
of the public endpoints against the committed tests/contract_keys.json, so a
backend field rename (mos → anything) turns THIS suite red before the frontend
ever sees it — the drift class the hermetic Playwright smoke cannot catch.

test_factors_backtest_contract also stands as the regression guard for the live
`_dt` NameError this batch fixed: the endpoint must return 200 with the
contracted shape (a bare 500 was the bug that shipped through three merges
because nothing hit this route)."""
import json
import os
from fastapi.testclient import TestClient
from app.main import app

CONTRACT = json.load(open(os.path.join(os.path.dirname(__file__), "contract_keys.json")))
client = TestClient(app)


def test_health_contract():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert set(r.json().keys()) == set(CONTRACT["/api/health"])


def test_factors_backtest_contract():
    r = client.get("/api/factors/backtest")
    assert r.status_code == 200, f"endpoint 500'd (the _dt NameError class): {r.text[:200]}"
    assert set(r.json().keys()) == set(CONTRACT["/api/factors/backtest"])
