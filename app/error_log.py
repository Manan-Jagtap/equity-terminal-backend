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


def errors_last_hour(db) -> int:
    """Count of captured errors in the trailing hour (for /api/health)."""
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
        return 0


def recent_errors(db, limit: int = 50) -> list[dict]:
    """Most-recent entries, newest first (admin surface)."""
    try:
        from app.manager_engine import _kv_get
        entries = _kv_get(db, KEY) or []
        return list(reversed(entries))[:limit]
    except Exception:
        return []
