"""Batch 34 — MoSPI MCP macro fetcher (keyless official CPI/WPI/IIP).

mcp.mospi.gov.in is the ministry's MCP server; we consume it as a PLAIN data
API (JSON-RPC over HTTP, SSE-framed) — no AI anywhere, consistent with the
AI-free doctrine. Mocked here: no network in tests."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")

import json
from app.database import SessionLocal, engine, Base
from app import models  # noqa: F401
from app import macro_data
from app import macro_sources as MS


def _sse(payload_text: str) -> str:
    body = {"jsonrpc": "2.0", "id": 1,
            "result": {"content": [{"type": "text", "text": payload_text}]}}
    return "event: message\ndata: " + json.dumps(body) + "\n"


class _Resp:
    def __init__(self, text): self.text = text
    def raise_for_status(self): pass
    def json(self): raise ValueError("SSE body")


_CPI_ROWS = {"data": [
    {"year": 2025, "month": "December", "state": "All India", "index": "198.0", "inflation": "1.33"},
    {"year": 2025, "month": "November", "state": "All India", "index": "197.9", "inflation": "0.71"},
], "msg": "ok"}
_IIP_ROWS = {"data": [
    {"year": 2026, "month": "May", "type": "General", "index": "122.7", "growth_rate": "5.1"},
    {"year": 2026, "month": "May", "type": "Sectoral", "index": "98.8"},   # must be skipped
], "msg": "ok"}
_WPI_ROWS = {"data": [
    {"year": 2026, "month": "April", "majorgroup": "Wholesale price index", "index_value": "167"},
    {"year": 2026, "month": "April", "majorgroup": "Primary articles", "index_value": "202"},
], "msg": "ok"}


def _mock_post(datasets: dict):
    def post(url, json=None, timeout=None, headers=None):
        ds = (((json or {}).get("params") or {}).get("arguments") or {}).get("dataset")
        payload = datasets.get(ds)
        if payload is None:
            return _Resp(_sse('{"error":"no such dataset"}'))
        import json as _j
        return _Resp(_sse(_j.dumps(payload)))
    return post


def test_fetch_parses_all_series_month_end(monkeypatch):
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    monkeypatch.setattr(MS.requests, "post",
                        _mock_post({"CPI": _CPI_ROWS, "IIP": _IIP_ROWS, "WPI": _WPI_ROWS}))
    n = MS.fetch_mospi_mcp(db)
    assert n > 0
    cpi = dict(macro_data.series(db, macro_data.CPI_2012))
    assert cpi.get("2025-12-31") == 198.0          # month-END convention (seed-compatible)
    iip = dict(macro_data.series(db, MS.IIP_2022_23))
    assert iip.get("2026-05-31") == 122.7          # Sectoral row skipped
    assert len([d for d in iip if d[:7] == "2026-05"]) == 1
    wpi = dict(macro_data.series(db, macro_data.WPI))
    assert wpi.get("2026-04-30") == 167.0          # headline only, Primary articles skipped
    db.close()


def test_kill_switch(monkeypatch):
    db = SessionLocal()
    monkeypatch.setenv("MOSPI_MCP", "0")
    called = []
    monkeypatch.setattr(MS.requests, "post", lambda *a, **k: called.append(1))
    assert MS.fetch_mospi_mcp(db) == 0
    assert not called                              # zero network with the switch off
    db.close()


def test_garbage_reply_never_raises(monkeypatch):
    db = SessionLocal()
    monkeypatch.setattr(MS.requests, "post", lambda *a, **k: _Resp("not sse at all"))
    assert MS.fetch_mospi_mcp(db) == 0             # fail-silent; seed carries on


def test_error_payload_skipped(monkeypatch):
    db = SessionLocal()
    monkeypatch.setattr(MS.requests, "post",
                        _mock_post({}))            # every dataset → error text
    assert MS.fetch_mospi_mcp(db) == 0
    db.close()
