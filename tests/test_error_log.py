"""Self-owned error telemetry: capture, scrubbing, counting, capping."""
import os, sys
import datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ["DATABASE_URL"] = "sqlite:////tmp/_pytest_errorlog.db"

import pytest
from app.database import Base, engine, SessionLocal
from app.error_log import record_error, errors_last_hour, recent_errors, KEY, MAX_ENTRIES


@pytest.fixture()
def db():
    Base.metadata.create_all(engine)
    s = SessionLocal()
    yield s
    s.close()
    Base.metadata.drop_all(engine)


def test_record_scrubs_query_string_and_counts(db):
    record_error(db, "/api/companies/TCS?token=SECRET&x=1", ValueError("boom"))
    rec = recent_errors(db)
    assert len(rec) == 1
    assert rec[0]["path"] == "/api/companies/TCS"       # query string stripped
    assert "SECRET" not in str(rec[0])
    assert rec[0]["type"] == "ValueError" and rec[0]["msg"] == "boom"
    assert errors_last_hour(db) == 1


def test_ring_buffer_caps(db):
    for i in range(MAX_ENTRIES + 30):
        record_error(db, f"/p/{i}", RuntimeError(f"e{i}"))
    rec = recent_errors(db, limit=MAX_ENTRIES + 30)
    assert len(rec) == MAX_ENTRIES
    assert rec[0]["path"] == f"/p/{MAX_ENTRIES + 29}"   # newest first


def test_old_entries_fall_out_of_hour_window(db):
    from app.manager_engine import _kv_put
    stale = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=3)).isoformat(timespec="seconds")
    _kv_put(db, KEY, [{"ts": stale, "path": "/old", "type": "X", "msg": "old"}])
    record_error(db, "/new", ValueError("fresh"))
    assert errors_last_hour(db) == 1                    # only the fresh one


def test_logger_never_raises_without_tables():
    # No tables created at all — record_error must swallow everything.
    s = SessionLocal()
    try:
        record_error(s, "/x", ValueError("y"))          # must not raise
        assert errors_last_hour(s) == 0
    finally:
        s.close()
