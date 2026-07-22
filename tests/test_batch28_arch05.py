"""Batch 28 — FIX-28 tail (part 8, final): ARCH-05 conservative dead-endpoint
prune. Only surface with ZERO frontend callers AND a confirmed-dead vendor path
was removed (/api/bse/*); every suspect with a live caller stays."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def _paths():
    return {r.path for r in app.routes if hasattr(r, "path")}


def test_bse_surface_removed():
    assert not any(p.startswith("/api/bse") for p in _paths())
    r = client.get("/api/bse/announcements/TCS", params={"quarter": "Q4FY26"})
    assert r.status_code == 404


def test_live_caller_suspects_retained():
    """The audit's static set-diff flagged these, but the frontend DOES call
    them (dynamic path construction) or they're deliberate ops surface —
    they must survive the prune."""
    paths = _paths()
    assert "/api/companies/{ticker}/balance_sheet" in paths
    assert "/api/companies/{ticker}/cash_flow" in paths
    assert "/api/admin/beta-state" in paths        # admin ops diagnostic, retained
