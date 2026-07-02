"""Unit tests for the durable monthly IndianAPI budget (app.api_budget).

Uses the shared test DB (kept consistent with the other suites via setdefault)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")

from app.database import Base, engine, SessionLocal
from app import models, api_budget as B


def _clean_session():
    Base.metadata.create_all(bind=engine)
    s = SessionLocal()
    s.query(models.ApiUsage).delete()
    s.commit()
    return s


def test_record_accumulates_and_reads(monkeypatch):
    monkeypatch.setenv("INDIANAPI_MONTHLY_BUDGET", "100")
    s = _clean_session()
    assert B.month_usage(s) == 0
    assert B.record_usage(s, 30) == 30
    assert B.record_usage(s, 20) == 50        # same month accumulates
    assert B.month_usage(s) == 50
    assert B.remaining(s) == 50
    s.close()


def test_zero_or_negative_is_noop_read(monkeypatch):
    monkeypatch.setenv("INDIANAPI_MONTHLY_BUDGET", "100")
    s = _clean_session()
    B.record_usage(s, 40)
    assert B.record_usage(s, 0) == 40
    assert B.record_usage(s, -5) == 40
    s.close()


def test_would_exceed_boundary(monkeypatch):
    monkeypatch.setenv("INDIANAPI_MONTHLY_BUDGET", "100")
    s = _clean_session()
    B.record_usage(s, 80)
    assert B.would_exceed(s, 25) is True      # 105 > 100
    assert B.would_exceed(s, 20) is False     # 100 is not > 100
    s.close()


def test_month_isolation(monkeypatch):
    monkeypatch.setenv("INDIANAPI_MONTHLY_BUDGET", "100")
    s = _clean_session()
    B.record_usage(s, 10, month="2099-01")
    assert B.month_usage(s, month="2099-01") == 10
    assert B.month_usage(s, month="2099-02") == 0   # different month, independent
    s.close()
