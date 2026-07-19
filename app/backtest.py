"""
app/backtest.py — the model's verifiable track record.

WHY THIS EXISTS
---------------
Every research product claims its calls work; almost none can prove it. This
module makes the terminal's verdicts FALSIFIABLE: the scheduler snapshots every
company's (verdict, price) daily, consecutive same-verdict days are compressed
into discrete CALLS (a call opens when the verdict changes and closes when it
changes again), and forward returns are measured per verdict cohort. The
headline statistic is the BUY−AVOID spread: if the model has signal, names it
called BUY should outperform names it called AVOID. No backfilled history, no
survivorship editing — the ledger starts the day tracking began and only ever
appends.

Pure functions (compress_calls / aggregate) are deliberately DB-free so the
math is unit-testable.
"""
from __future__ import annotations
from datetime import date as _date
from statistics import median

from . import models
from . import corporate_actions as ca

# Cohorts with a directional opinion. NO DATA / LOW CONF are excluded from the
# scorecard (the model explicitly declined to call them) but still snapshotted
# so coverage gaps are visible and honest.
ACTIONABLE = ("BUY", "ACCUMULATE", "HOLD", "REDUCE", "AVOID")


# ── Snapshot writer ──────────────────────────────────────────────────────────

def take_snapshots(db) -> int:
    """Upsert today's (verdict, price) row for every company with a precomputed
    valuation and a live price. Idempotent per (company, date)."""
    today = _date.today().isoformat()
    price_by = {m.company_id: m.price for m in db.query(models.MarketSnapshot).all()}
    existing = {(s.company_id, s.date): s
                for s in db.query(models.VerdictSnapshot).filter_by(date=today).all()}
    n = 0
    for v in db.query(models.Valuation).all():
        price = price_by.get(v.company_id)
        if not price or price <= 0 or not v.verdict:
            continue
        co = db.query(models.Company).get(v.company_id)
        if co is None:
            continue
        row = existing.get((v.company_id, today))
        payload = dict(ticker=(co.ticker or "").upper(), price=price,
                       intrinsic=v.intrinsic, mos=v.mos, verdict=v.verdict,
                       composite=v.composite, confidence=v.confidence,
                       valuation_sector=v.valuation_sector)
        if row:
            for k, val in payload.items():
                setattr(row, k, val)
        else:
            db.add(models.VerdictSnapshot(company_id=v.company_id, date=today, **payload))
        n += 1
    db.commit()
    return n


# ── Pure call-ledger math (unit-tested) ─────────────────────────────────────

def compress_calls(snaps: list[dict], latest_price: float | None,
                   actions: list[dict] | None = None) -> list[dict]:
    """Compress one company's date-ascending snapshots into discrete calls.

    A call opens at the FIRST snapshot of a verdict run and closes at the first
    snapshot of a DIFFERENT verdict (closing price = price at the change). The
    final run stays OPEN and is marked to `latest_price`. Returns date-ordered
    call dicts with entry/exit, return and holding days.

    `actions` (the company's CorporateAction rows as dicts) is optional: when
    supplied, each call also carries a split/bonus- and dividend-adjusted
    `total_ret`. Omitting it reproduces the legacy price-only behaviour exactly."""
    calls: list[dict] = []
    run_start = None
    for s in snaps:
        if run_start is None or s["verdict"] != run_start["verdict"]:
            if run_start is not None:
                calls.append(_close(run_start, s["date"], s["price"], open_=False, actions=actions))
            run_start = s
    if run_start is not None:
        calls.append(_close(run_start, None,
                            latest_price if latest_price else run_start["price"],
                            open_=True, actions=actions))
    return calls


def _days(d0: str, d1: str | None) -> int:
    a = _date.fromisoformat(d0)
    b = _date.fromisoformat(d1) if d1 else _date.today()
    return max(0, (b - a).days)


def _close(start: dict, end_date: str | None, end_price: float, open_: bool,
           actions: list[dict] | None = None) -> dict:
    sp = start["price"]
    ret = (end_price / sp - 1) if sp else None      # raw price return (legacy)
    # Total return = split/bonus-adjusted price move + dividends over the window.
    # With no actions supplied it collapses to the price return, so `total_ret`
    # is always safe to read.
    total_ret, div_ret = ret, (0.0 if ret is not None else None)
    if sp and actions:
        tr = ca.total_return(start["date"], end_date, sp, end_price, actions)
        if tr:
            total_ret, div_ret = tr["total"], tr["dividend"]
    return {
        "ticker": start.get("ticker"), "verdict": start["verdict"],
        "start_date": start["date"], "start_price": sp,
        "end_date": end_date, "end_price": end_price,
        "ret": ret, "total_ret": total_ret, "div_ret": div_ret,
        "days": _days(start["date"], end_date),
        "open": open_,
        "mos_at_call": start.get("mos"), "intrinsic_at_call": start.get("intrinsic"),
        "sector": start.get("valuation_sector"),
    }


def aggregate(calls: list[dict]) -> dict:
    """Per-cohort stats + the BUY−AVOID spread. A 'win' is direction-correct:
    positive return for BUY/ACCUMULATE, NEGATIVE return for REDUCE/AVOID (the
    model said stay away — it is right when the stock falls). HOLD has no
    direction, so no win rate."""
    cohorts: dict = {}
    for v in ACTIONABLE:
        rows = [c for c in calls if c["verdict"] == v and c["ret"] is not None]
        rets = [c["ret"] for c in rows]
        # total_ret falls back to ret when a call predates the corporate-action
        # wiring (or none applied), so the total cohort is always populated.
        trets = [c["total_ret"] if c.get("total_ret") is not None else c["ret"] for c in rows]
        if v in ("BUY", "ACCUMULATE"):
            wins = sum(1 for r in rets if r > 0)
        elif v in ("REDUCE", "AVOID"):
            wins = sum(1 for r in rets if r < 0)
        else:
            wins = None
        cohorts[v] = {
            "n": len(rows),
            "open": sum(1 for c in rows if c["open"]),
            "avg_return": sum(rets) / len(rets) if rets else None,
            "median_return": median(rets) if rets else None,
            "avg_total_return": sum(trets) / len(trets) if trets else None,
            "median_total_return": median(trets) if trets else None,
            "win_rate": (wins / len(rets)) if (wins is not None and rets) else None,
            "avg_days": sum(c["days"] for c in rows) / len(rows) if rows else None,
        }
    b, a = cohorts["BUY"]["avg_return"], cohorts["AVOID"]["avg_return"]
    tb, ta = cohorts["BUY"]["avg_total_return"], cohorts["AVOID"]["avg_total_return"]
    return {"cohorts": cohorts,
            "buy_avoid_spread": (b - a) if (b is not None and a is not None) else None,
            "total_buy_avoid_spread": (tb - ta) if (tb is not None and ta is not None) else None}


# ── Full computation over the DB ─────────────────────────────────────────────

def _actions_by_company(db) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for a in db.query(models.CorporateAction).all():
        out.setdefault(a.company_id, []).append(
            {"action_type": a.action_type, "ex_date": a.ex_date,
             "value": a.value, "ratio": a.ratio})
    return out


def _universe_benchmark(by_co, price_by, actions_by) -> dict | None:
    """Equal-weight total return of the WHOLE tracked universe from each name's
    first snapshot to today — the honest 'what if you just owned everything we
    track' comparator for the BUY cohort. NOT the NSE NIFTY-TRI series (that
    needs a vendor feed); labelled as such so it is never misread."""
    rets = []
    for cid, rows in by_co.items():
        if not rows:
            continue
        first, last_price = rows[0], price_by.get(cid)
        if not last_price:
            continue
        tr = ca.total_return(first["date"], None, first["price"], last_price, actions_by.get(cid))
        if tr:
            rets.append(tr["total"])
    if not rets:
        return None
    return {"total_return": sum(rets) / len(rets), "n": len(rets),
            "basis": "equal-weight tracked universe, first snapshot → today, incl. dividends"}


# ── #120: backtest-calibrated confidence (sample-gated, advisory) ────────────
# We DON'T wire this into the live confidence yet: the track record is weeks old
# (DAT-05), so calibrating on it now would overfit to statistical noise (and, per
# the audit, would currently penalise the BUY cohort for a 31-day blip). Instead
# we compute the calibration table and gate it — each bucket reports whether it
# has ENOUGH history to trust, and a `ready` flag flips only when the whole set
# clears the thresholds. When it does, `suggested_multiplier` is what a future
# release would apply to the shown confidence for that bucket.
_CALIB_MIN_CALLS = 30      # per bucket
_CALIB_MIN_DAYS = 90       # of tracking history


def calibrate(scored: list[dict], snapshot_days: int) -> dict:
    """Realized direction-accuracy by (verdict, MoS band), with a sufficiency
    gate. Read-only/advisory until `ready`."""
    def band(mos):
        if mos is None: return "na"
        if mos <= 0.10: return "0-10%"
        if mos <= 0.25: return "10-25%"
        if mos <= 0.50: return "25-50%"
        return "50%+"

    buckets: dict = {}
    for c in scored:
        if c.get("ret") is None:
            continue
        key = f'{c["verdict"]}|{band(c.get("mos"))}'
        b = buckets.setdefault(key, {"n": 0, "wins": 0, "sum_ret": 0.0})
        b["n"] += 1
        b["sum_ret"] += c["ret"]
        bullish = c["verdict"] in ("BUY", "ACCUMULATE")
        if (bullish and c["ret"] > 0) or (not bullish and c["ret"] < 0):
            b["wins"] += 1

    rows = {}
    all_ready = bool(buckets) and snapshot_days >= _CALIB_MIN_DAYS
    for key, b in sorted(buckets.items()):
        hit = b["wins"] / b["n"] if b["n"] else None
        enough = b["n"] >= _CALIB_MIN_CALLS and snapshot_days >= _CALIB_MIN_DAYS
        all_ready = all_ready and enough
        rows[key] = {
            "n": b["n"], "hit_rate": hit,
            "avg_return": b["sum_ret"] / b["n"] if b["n"] else None,
            "sufficient_sample": enough,
            # A hit rate materially below chance (0.5) argues for de-rating that
            # bucket's confidence; above, for keeping it. Only meaningful once
            # `sufficient_sample`; surfaced always for transparency.
            "suggested_multiplier": (round(0.6 + 0.8 * hit, 2)
                                     if (enough and hit is not None) else None),
        }
    return {
        "ready": all_ready,
        "reason": (None if all_ready else
                   f"insufficient history: need ≥{_CALIB_MIN_DAYS} tracking days "
                   f"(have {snapshot_days}) and ≥{_CALIB_MIN_CALLS} calls/bucket "
                   "before calibration is applied to live confidence"),
        "min_calls_per_bucket": _CALIB_MIN_CALLS,
        "min_days": _CALIB_MIN_DAYS,
        "buckets": rows,
    }


def compute_backtest(db) -> dict:
    snaps = (db.query(models.VerdictSnapshot)
               .order_by(models.VerdictSnapshot.company_id,
                         models.VerdictSnapshot.date).all())
    price_by = {m.company_id: m.price for m in db.query(models.MarketSnapshot).all()}
    actions_by = _actions_by_company(db)

    by_co: dict[int, list[dict]] = {}
    for s in snaps:
        by_co.setdefault(s.company_id, []).append({
            "ticker": s.ticker, "date": s.date, "price": s.price,
            "verdict": s.verdict, "mos": s.mos, "intrinsic": s.intrinsic,
            "valuation_sector": s.valuation_sector,
        })

    all_calls: list[dict] = []
    for cid, rows in by_co.items():
        all_calls += compress_calls(rows, price_by.get(cid), actions_by.get(cid))

    scored = [c for c in all_calls if c["verdict"] in ACTIONABLE]
    agg = aggregate(scored)
    dates = [s.date for s in snaps]
    return {
        "as_of": _date.today().isoformat(),
        "tracking_since": min(dates) if dates else None,
        "snapshot_days": len(set(dates)),
        "companies_tracked": len(by_co),
        "total_calls": len(scored),
        "excluded_no_call": sum(1 for c in all_calls if c["verdict"] not in ACTIONABLE),
        "benchmark": _universe_benchmark(by_co, price_by, actions_by),
        "calibration": calibrate(scored, len(set(dates))),   # #120 (advisory/gated)
        **agg,
        # Per-call ledger, newest first, worst-kept-in: full transparency.
        "calls": sorted(scored, key=lambda c: (c["start_date"], c["ticker"] or ""),
                        reverse=True),
    }
