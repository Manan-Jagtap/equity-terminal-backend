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


# ── Call OUTCOMES (separate from tick(), which counts SPEND) ──────────────────
#
# 14 Aug 2026: every IndianAPI call failed for 5+ hours while /api/health
# reported {"status":"ok","errors_1h":0}. Nothing was lying — _get() serves its
# last good payload on upstream failure, which is the right behaviour, and no
# exception reached the error log. But "we are serving cache because upstream is
# gone" and "we are healthy" were indistinguishable from outside, so a revoked
# credential looked like a quiet evening. The only surfaces that told the truth
# were the ones with no cache to fall back on (gainers/losers, 52-week, IPOs) —
# and those read as "market may be closed".
#
# Spend and success are different questions. tick() answers "how much of the
# quota did we burn"; a failed call still burns quota, so tick() cannot tell you
# the feed is down. These counters answer "is upstream actually answering".
#
# In-process and per-container by design: web and scheduler are separate
# processes with separate counters, and /api/health runs in web. That is enough
# to catch a dead credential or a dead upstream, because web makes vendor calls
# on every market route. It is NOT a cross-process view — a scheduler-only
# failure will not appear here.
_ok = 0
_fail = 0
_last_ok = None      # monotonic-ish wall clock of the last SUCCESSFUL call
_last_fail = None


def record(success: bool) -> None:
    """Record a vendor call outcome. Cheap, thread-safe, never raises."""
    global _ok, _fail, _last_ok, _last_fail
    import time as _t
    with _lock:
        if success:
            _ok += 1
            _last_ok = _t.time()
        else:
            _fail += 1
            _last_fail = _t.time()


def outcomes() -> dict:
    """{ok, fail, last_ok_min, last_fail_min} — ages in minutes, None if never."""
    import time as _t
    now = _t.time()
    with _lock:
        return {
            "ok": _ok,
            "fail": _fail,
            "last_ok_min": None if _last_ok is None else round((now - _last_ok) / 60),
            "last_fail_min": None if _last_fail is None else round((now - _last_fail) / 60),
        }
