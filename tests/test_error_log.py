"""Self-owned error telemetry: capture, scrubbing, counting, capping."""
import os, sys
import datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ["DATABASE_URL"] = "sqlite:////tmp/_pytest_errorlog.db"

import pytest
from app.database import Base, engine, SessionLocal
from app.error_log import (record_error, errors_last_hour, error_hours_last_day,
                           recent_errors, KEY, MAX_ENTRIES)


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
        assert error_hours_last_day(s) == 0             # lenient: a DB error reads 0…
        with pytest.raises(Exception):
            error_hours_last_day(s, strict=True)        # …strict (health) lets it propagate
    finally:
        s.close()


# ── error_hours_last_day: persistence, not intensity ─────────────────────────

def _put(db, offsets_min):
    """Store one entry per offset (minutes before now), UTC ISO like record_error."""
    from app.manager_engine import _kv_put
    now = dt.datetime.now(dt.timezone.utc)
    _kv_put(db, KEY, [{"ts": (now - dt.timedelta(minutes=m)).isoformat(timespec="seconds"),
                       "path": "/p", "type": "X", "msg": "m"} for m in offsets_min])


def test_a_burst_is_one_hour_however_large(db):
    """The property that makes this gate safe: 15 errors in the same minute
    are ONE bucket — a storm (errors_1h's case) cannot keep the sustained rule
    red for a day after it ends."""
    _put(db, [0] * 15)
    assert errors_last_hour(db) == 15
    assert error_hours_last_day(db) == 1


def test_a_stream_of_one_an_hour_fills_the_day(db):
    """The exact hole: one failure every hour (a job failing on every run)
    never nears the storm threshold in any single hour but marks every bucket.
    Entries older than 24 h do not count (30 hourly marks → 24 in-window)."""
    _put(db, [60 * h + 30 for h in range(30)])           # one mark per hour, 30 h back
    assert errors_last_hour(db) == 1                     # invisible to the storm rule
    assert error_hours_last_day(db) == 24                # 60-min spacing → 24 distinct buckets


def test_hours_are_utc_clock_buckets_across_offsets(db):
    """Two entries in the same UTC hour written with different offsets are one
    bucket — normalise to UTC before bucketing, or a mixed-writer ring
    double-counts."""
    from app.manager_engine import _kv_put
    now = dt.datetime.now(dt.timezone.utc).replace(minute=10, second=0, microsecond=0)
    if now > dt.datetime.now(dt.timezone.utc):           # keep both entries in the past
        now -= dt.timedelta(hours=1)
    ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
    _kv_put(db, KEY, [{"ts": now.isoformat(), "path": "/a", "type": "X", "msg": ""},
                      {"ts": (now + dt.timedelta(minutes=5)).astimezone(ist).isoformat(),
                       "path": "/b", "type": "X", "msg": ""}])
    assert error_hours_last_day(db) == 1
