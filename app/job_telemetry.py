"""FIX-06/ARCH-03 — surface swallowed scheduled-job failures into error_log.

The scheduler's jobs catch their own exceptions and `log.error(...)` them, so a
failing nightly job never touched `errors_1h` and stayed invisible to
/api/health and the uptime alert (the same silence that froze the ledger for 5
days). `record_job_error` funnels those caught exceptions into error_log so they
count toward `errors_1h` → health → the FIX-05 alert. Best-effort and never
raises — telemetry must not escalate a job failure into a crash. Lives here (not
in scheduler.py) so it is importable/testable: scheduler.py runs a `while True`
loop at import time."""
from __future__ import annotations


def record_job_error(exc: BaseException, tag: str | None = None) -> None:
    if tag is None:
        try:
            import inspect
            tag = "job:" + inspect.stack()[1].function
        except Exception:
            tag = "job:unknown"
    try:
        from app.database import SessionLocal
        from app.error_log import record_error
        s = SessionLocal()
        try:
            record_error(s, tag, exc)
        finally:
            s.close()
    except Exception:
        pass
