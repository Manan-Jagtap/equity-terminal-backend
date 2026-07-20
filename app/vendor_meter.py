"""Central IndianAPI call meter (FIX-07 / DATA-04).

Every outbound IndianAPI GET across every module ticks ONE in-process counter;
the accumulated delta is flushed to the durable monthly tally
(api_budget / models.ApiUsage) at natural boundaries — each web request
(middleware) and each scheduler heartbeat.

Before this, only the bulk ingester's primary /stock GET was counted, while its
~9 best-effort insight calls per name (_get_safe) and every on-demand route
(market / mf / news / ipo / logo / profile / FM) were not — so the budget guard
governed on ~10-15% of real spend and could not protect the plan cutover on
~11 Aug 2026. Now the metered count tracks the vendor dashboard.
"""
from __future__ import annotations
import threading

_lock = threading.Lock()
_pending = 0    # ticks not yet flushed to the durable tally
_total = 0      # cumulative ticks this process — display/logging only


def tick(n: int = 1) -> None:
    """Count `n` vendor calls (default 1). Cheap, thread-safe, never raises."""
    global _pending, _total
    if n <= 0:
        return
    with _lock:
        _pending += n
        _total += n


def pending() -> int:
    return _pending


def total() -> int:
    return _total


def drain() -> int:
    global _pending
    with _lock:
        n, _pending = _pending, 0
    return n


def flush(db) -> int:
    """Persist pending ticks to the monthly ApiUsage tally; no-op at 0. Never
    raises — a metering hiccup must not break a request or a job. On a DB error
    the ticks are returned to the pending pool so they are not lost."""
    n = drain()
    if n <= 0:
        return 0
    try:
        from app import api_budget
        api_budget.record_usage(db, n)
        return n
    except Exception:
        global _pending
        with _lock:
            _pending += n
        return 0
