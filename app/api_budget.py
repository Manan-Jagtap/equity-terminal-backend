"""
app/api_budget.py — durable monthly IndianAPI call budget.

IndianAPI exposes no usage meter and the dev-plan quota (~10k calls/month) is a
hard ceiling. The ingester counts its own outbound calls in-process
(indianapi_ingester.get_call_count); this module persists a per-month total
(models.ApiUsage) and lets bulk jobs pre-flight a large batch BEFORE spending
it, so a misconfigured cadence or a manual full-refresh over the whole universe
can't silently blow the month.

Pure, DB-backed helpers — unit-tested against a throwaway SQLite session.
"""
from __future__ import annotations
import datetime as _dt
import os

from . import models

# Rough per-name cost of a full ingest (1 /stock + ~9 best-effort insight calls);
# used only to project a bulk run's spend for the pre-flight log.
CALLS_PER_FULL_INGEST = 10


def current_month() -> str:
    return _dt.date.today().strftime("%Y-%m")


def budget() -> int:
    return int(os.getenv("INDIANAPI_MONTHLY_BUDGET", "10000"))


def month_usage(db, month: str | None = None) -> int:
    row = db.query(models.ApiUsage).filter_by(month=month or current_month()).first()
    return int(row.calls) if row else 0


def record_usage(db, n: int, month: str | None = None) -> int:
    """Add `n` calls to the month tally (creating the row if needed). Returns the
    new monthly total. A non-positive `n` is a no-op read."""
    m = month or current_month()
    if n <= 0:
        return month_usage(db, m)
    row = db.query(models.ApiUsage).filter_by(month=m).first()
    if row:
        row.calls = int(row.calls) + int(n)
    else:
        row = models.ApiUsage(month=m, calls=int(n))
        db.add(row)
    db.commit()
    return int(row.calls)


def remaining(db, month: str | None = None) -> int:
    return max(0, budget() - month_usage(db, month))


def would_exceed(db, projected: int, month: str | None = None) -> bool:
    """True if spending `projected` more calls this month would breach budget."""
    return (month_usage(db, month) + max(0, projected)) > budget()
