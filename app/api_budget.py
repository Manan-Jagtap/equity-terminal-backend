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


def cycle_day() -> int:
    """Day of month the vendor plan RENEWS on. 1 = calendar month."""
    try:
        return max(1, min(28, int(os.getenv("INDIANAPI_CYCLE_DAY", "1"))))
    except ValueError:
        return 1


def current_month(today: _dt.date | None = None) -> str:
    """The key for the CURRENT BILLING CYCLE — not necessarily the calendar month.

    The vendor resets its counter on the plan's renewal day, and ours must reset
    with it or the two disagree in whichever direction the calendar happens to
    fall. Measured on 2026-08-12, the day after an 11th-of-month renewal: the
    vendor console read 0 used / 10,000 remaining while this table read 9,081,
    because our bucket ("2026-08") still carried ten days of PRE-renewal spend.

    Over-counting is the safe direction. The dangerous one arrives on the 1st of
    each calendar month, when a calendar bucket resets to zero while the vendor's
    cycle is ~20 days old and mostly spent — the guard would then permit a full
    month's budget on top of a nearly exhausted plan.

    With INDIANAPI_CYCLE_DAY=11, spend from 11 Aug to 10 Sep keys to "2026-08".
    Default 1 preserves the old calendar-month behaviour exactly, so this is
    inert until the renewal day is configured.
    """
    d = today or _dt.date.today()
    anchor = cycle_day()
    if anchor > 1 and d.day < anchor:
        # Before this month's renewal → still inside the PREVIOUS cycle.
        y, m = (d.year - 1, 12) if d.month == 1 else (d.year, d.month - 1)
        return f"{y:04d}-{m:02d}"
    return d.strftime("%Y-%m")


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
