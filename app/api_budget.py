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

# Rough per-name cost of a full ingest; projects a bulk run's spend for the
# pre-flight in indianapi_ingester.run() — which is a GATE, not just a log line.
#
# 10 = 1 /stock + ~9 best-effort insight calls, and all ~9 are issued again.
#
# It was cut to 7 on 24 Jul 2026 on the DATA-12 reasoning: four of those nine
# (three /historical_stats — ratios, profit_loss_stats, quarter_results — and
# one /documents) looked like endpoints the vendor had taken off-plan, so
# projecting them looked like refusing ~1,000-name runs over ~4,000 calls that
# were never made.
#
# CORRECTED 25 Aug 2026: nothing was ever taken off-plan. We were calling the
# SHARED host (stock.indianapi.in) with a Developer-plan key instead of the
# plan's DEDICATED host (dev.indianapi.in), and read the wrong host's answers
# as a withdrawal. On dev both endpoints answer, the _ON_PLAN flags in
# indianapi_ingester are back on, and all ~10 calls per name are spent again —
# so the estimate is 10. Re-derive it only if those flags are ever switched off
# for a reason a manual probe confirmed.
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


# ── Per-CALL ceiling ─────────────────────────────────────────────────────────
#
# would_exceed() above is a PRE-FLIGHT: a batch entry point projects a whole run
# and refuses before starting (full ingest, backfill, profile refresh, MF). It
# governs nothing once a run is under way, and nothing at all for code that
# reaches the vendor helpers directly.
#
# On 26 Aug 2026 a one-off repair did exactly that — ~1,600 _get_safe calls with
# no pre-flight — and sailed past the ceiling to 9,567 of 9,500. Nothing stopped
# it because nothing was asking. FIX-07 had made those calls COUNTED; it never
# made them GATED.
#
# The guard has to be cheap enough to sit in front of every single call, so it
# caches the verdict for _CEILING_TTL_S and opens its own short-lived session
# (the vendor helpers are module-level and hold no db). Worst case overshoot is
# one TTL of traffic, which is bounded and tiny next to a whole month.
#
# It fails OPEN on any error: a metering hiccup must not halt ingest. That is
# not the hole that caused the overrun — the hole was having no guard at all.
_CEILING_TTL_S = 60.0
_ceiling_at = 0.0
_ceiling_over = False


def over_budget(force: bool = False) -> bool:
    """True when this month's usage has reached the budget. Cheap (cached
    _CEILING_TTL_S), thread-safe enough for the purpose, never raises."""
    global _ceiling_at, _ceiling_over
    import time as _t
    now = _t.time()
    if not force and (now - _ceiling_at) < _CEILING_TTL_S:
        return _ceiling_over
    try:
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            over = remaining(db) <= 0
        finally:
            db.close()
    except Exception:
        return False                      # fail open — never halt on a metering fault
    _ceiling_over, _ceiling_at = bool(over), now
    return _ceiling_over


def _reset_ceiling_cache() -> None:
    """Test hook: drop the cached verdict so the next call re-reads."""
    global _ceiling_at, _ceiling_over
    _ceiling_at, _ceiling_over = 0.0, False
