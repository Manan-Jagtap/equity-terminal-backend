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
from collections import deque

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

# FAILURE-RATE FLOOR. The since-boot totals above feed health's
# "vendor_unreachable" rule (failures AND no success in 30 min). That rule is
# deliberately conservative, and it has a hole: one lucky success every half
# hour clears it, so a vendor answering 1 call in 20 reads as healthy for as
# long as the luck holds. A ring of the last RING_N outcomes answers the other
# question — "what fraction of RECENT calls failed" — independent of when the
# last success happened. `record()` feeds both; `failing()` reads the ring.
RING_N = 20
FLOOR_MIN_CALLS = 10      # below this the ring is too thin to judge — stay "ok"
FLOOR_FAIL_PCT = 0.80     # >= 80% of the last RING_N failed → degrade
_ring = deque(maxlen=RING_N)


def record(success: bool) -> None:
    """Record a vendor call outcome. Cheap, thread-safe, never raises.

    Feeds BOTH the since-boot totals (outcomes()) and the recent-outcome ring
    (recent()/failing()) — one call site, two views, so a caller can never keep
    one signal honest and starve the other."""
    global _ok, _fail, _last_ok, _last_fail
    import time as _t
    with _lock:
        if success:
            _ok += 1
            _last_ok = _t.time()
        else:
            _fail += 1
            _last_fail = _t.time()
        _ring.append(bool(success))


# ── Payload judgement: what counts as a SUCCESSFUL answer ────────────────────
#
# record() takes a bool, and before this every call site had its own idea of
# what a good answer looked like — most of them "data is not None", which reads
# a 200 wrapping {} or {"error": ...} as success. market_routes._payload_ok
# settled the rule for the market feeds (#140): a 200 is not a success until the
# body carries data, because the vendor answers some failure modes with 200 +
# an empty or error-shaped body. That rule is right for feeds that are never
# legitimately empty. It is WRONG for lookups keyed by a name the caller chose
# — a fund search that matches nothing, a small company with no rated debt, a
# forecast for a name nobody covers — where an empty answer IS the vendor
# answering correctly. Recording those as failures would let a junk query on
# an unauthenticated route, or a quiet name, push /api/health to "degraded".
# `empty_ok` is that distinction, chosen per call site.
_ENVELOPE_KEYS = frozenset({"error", "message", "detail"})


def payload_ok(data, *, empty_ok: bool = False) -> bool:
    """Judge a parsed vendor body: True when upstream actually answered.

      None                 → False   non-200 / transport error / unparseable
                                     (call sites collapse all three to None)
      {} / [] / ""         → empty_ok
      {"error": ...}       → False   the vendor's own failure, wrapped in a 200
      [{"error": ...}, N]  → False   the same envelope as the insight endpoints
                                     ship it (list-wrapped, HTTP code last)
      anything else        → True

    "info" is deliberately NOT an envelope key. Since 24 Jul 2026 (DATA-12) the
    vendor answers /historical_stats for every name with 200 {"info": "Not a
    valid script_code"} — an off-plan endpoint, not a dead vendor — and the
    Financials tabs still call it on every visit. Counting that as a failure
    would report the vendor as failing whenever anyone browsed. Pure; never
    raises on JSON-shaped input."""
    if data is None:
        return False
    if not data:                                    # {}, [], "", 0, False
        return empty_ok
    if isinstance(data, dict):
        return not (set(data.keys()) <= _ENVELOPE_KEYS)
    if isinstance(data, list):
        head, tail = data[0], data[-1]
        if isinstance(head, dict) and "error" in head:
            return False
        if isinstance(tail, int) and not isinstance(tail, bool) and tail >= 400:
            return False
    return True


def recent() -> dict:
    """{n, fail, fail_pct} over the last RING_N recorded outcomes. fail_pct is
    None until anything is recorded (no evidence is not bad evidence)."""
    with _lock:
        n = len(_ring)
        f = sum(1 for s in _ring if not s)
    return {"n": n, "fail": f, "fail_pct": (None if n == 0 else f / n)}


def failing(min_calls: int = FLOOR_MIN_CALLS, min_fail_pct: float = FLOOR_FAIL_PCT) -> bool:
    """True when the recent ring holds >= min_calls outcomes and >= min_fail_pct
    of them failed — even if the LAST call happened to succeed. Thin evidence
    (fewer than min_calls) never trips it: a fresh container that has made three
    calls, all failed, is the unreachable rule's job, not this one's."""
    r = recent()
    return r["n"] >= min_calls and r["fail_pct"] is not None and r["fail_pct"] >= min_fail_pct


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
