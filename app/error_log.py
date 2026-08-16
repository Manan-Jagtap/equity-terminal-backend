"""
app/error_log.py — SELF-OWNED error telemetry (no third-party processor).

Chosen over Sentry deliberately: the owner's DPDP posture is that data must not
flow to processors outside India, so unhandled exceptions are captured into OUR
OWN database (a capped ring buffer in kv_store) and surfaced two ways:

  · /api/health carries `errors_1h` — a bare count, safe to expose — which the
    GitHub uptime workflow thresholds, so an error storm emails the owner just
    like downtime does;
  · GET /api/admin/errors shows the recent entries (admin-gated).

Personal-data-safe by construction: each entry stores only a UTC timestamp, the
request path WITHOUT its query string, the exception class and a truncated
message — never IPs, users, headers, bodies or tokens.
"""
from __future__ import annotations
import datetime as _dt

KEY = "error_log_v1"
MAX_ENTRIES = 100


def record_error(db, path: str, exc: BaseException) -> None:
    """Append one entry to the ring buffer. Must NEVER raise — an error logger
    that masks the original error would be worse than none."""
    try:
        from app.manager_engine import _kv_get, _kv_put
        entries = _kv_get(db, KEY) or []
        if not isinstance(entries, list):
            entries = []
        entries.append({
            "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "path": (path or "").split("?")[0][:120],
            "type": type(exc).__name__[:60],
            "msg": str(exc)[:200],
        })
        _kv_put(db, KEY, entries[-MAX_ENTRIES:])
    except Exception:
        pass


def errors_last_hour(db, *, strict: bool = False) -> int:
    """Count of captured errors in the trailing hour (for /api/health).

    Default: never raises — a DB hiccup reads as 0, which is right for the
    admin page (a best-effort figure next to the entry list). `strict=True` is
    for /api/health, where a swallowed DB error is exactly the failure being
    measured: with the database down this used to answer 0 ("no errors") while
    the store it counts from was unreachable. Under strict the DB error
    propagates so health can mark the signal UNMEASURED instead of "clean"."""
    try:
        from app.manager_engine import _kv_get
        entries = _kv_get(db, KEY) or []
        cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=1)
        n = 0
        for e in entries:
            try:
                ts = _dt.datetime.fromisoformat(e["ts"])
                if ts >= cutoff:
                    n += 1
            except Exception:
                continue
        return n
    except Exception:
        if strict:
            raise
        return 0


def error_hours_last_day(db, *, strict: bool = False) -> int:
    """How many distinct clock hours (UTC) in the trailing 24 h hold at least
    one captured error — PERSISTENCE, where errors_last_hour is INTENSITY.

    Why a second window: the uptime rule on errors_1h (> 25) catches a storm
    and nothing quieter. A job that fails on EVERY run (run_intraday_prices
    every 90 min, a nightly wrapper) or a route broken for every caller writes
    a handful of entries an hour, every hour, and never nears 25 in any single
    hour — the shape behind the 5-day ledger freeze, still invisible to the
    alert after FIX-06 made those failures count. Hours-with-errors rather
    than errors-in-24h on purpose: a burst is one or two buckets however large
    (the storm rule already paged it), so a resolved storm does not keep the
    alert red for a day; a standing fault is most of the buckets. Bounded
    0..25 (24 whole hours + the two partial edge hours), so a threshold reads
    as a share of the day. The ring keeps MAX_ENTRIES=100, so under a storm it
    spans less than a day — fine: that is errors_1h's case, and a stream of
    one an hour reaches back 100 h. Same strict/lenient contract as
    errors_last_hour: /api/health passes strict=True so a DB error is reported
    as UNMEASURED, never as a clean 0."""
    try:
        from app.manager_engine import _kv_get
        entries = _kv_get(db, KEY) or []
        cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=24)
        hours = set()
        for e in entries:
            try:
                ts = _dt.datetime.fromisoformat(e["ts"])
                if ts >= cutoff:
                    hours.add(ts.astimezone(_dt.timezone.utc)
                                .replace(minute=0, second=0, microsecond=0))
            except Exception:
                continue
        return len(hours)
    except Exception:
        if strict:
            raise
        return 0


def recent_errors(db, limit: int = 50) -> list[dict]:
    """Most-recent entries, newest first (admin surface)."""
    try:
        from app.manager_engine import _kv_get
        entries = _kv_get(db, KEY) or []
        return list(reversed(entries))[:limit]
    except Exception:
        return []
