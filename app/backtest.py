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

def compress_calls(snaps: list[dict], latest_price: float | None) -> list[dict]:
    """Compress one company's date-ascending snapshots into discrete calls.

    A call opens at the FIRST snapshot of a verdict run and closes at the first
    snapshot of a DIFFERENT verdict (closing price = price at the change). The
    final run stays OPEN and is marked to `latest_price`. Returns date-ordered
    call dicts with entry/exit, return and holding days."""
    calls: list[dict] = []
    run_start = None
    for s in snaps:
        if run_start is None or s["verdict"] != run_start["verdict"]:
            if run_start is not None:
                calls.append(_close(run_start, s["date"], s["price"], open_=False))
            run_start = s
    if run_start is not None:
        calls.append(_close(run_start, None,
                            latest_price if latest_price else run_start["price"],
                            open_=True))
    return calls


def _days(d0: str, d1: str | None) -> int:
    a = _date.fromisoformat(d0)
    b = _date.fromisoformat(d1) if d1 else _date.today()
    return max(0, (b - a).days)


def _close(start: dict, end_date: str | None, end_price: float, open_: bool) -> dict:
    ret = (end_price / start["price"] - 1) if start["price"] else None
    return {
        "ticker": start.get("ticker"), "verdict": start["verdict"],
        "start_date": start["date"], "start_price": start["price"],
        "end_date": end_date, "end_price": end_price,
        "ret": ret, "days": _days(start["date"], end_date),
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
            "win_rate": (wins / len(rets)) if (wins is not None and rets) else None,
            "avg_days": sum(c["days"] for c in rows) / len(rows) if rows else None,
        }
    b, a = cohorts["BUY"]["avg_return"], cohorts["AVOID"]["avg_return"]
    return {"cohorts": cohorts,
            "buy_avoid_spread": (b - a) if (b is not None and a is not None) else None}


# ── Full computation over the DB ─────────────────────────────────────────────

def compute_backtest(db) -> dict:
    snaps = (db.query(models.VerdictSnapshot)
               .order_by(models.VerdictSnapshot.company_id,
                         models.VerdictSnapshot.date).all())
    price_by = {m.company_id: m.price for m in db.query(models.MarketSnapshot).all()}

    by_co: dict[int, list[dict]] = {}
    for s in snaps:
        by_co.setdefault(s.company_id, []).append({
            "ticker": s.ticker, "date": s.date, "price": s.price,
            "verdict": s.verdict, "mos": s.mos, "intrinsic": s.intrinsic,
            "valuation_sector": s.valuation_sector,
        })

    all_calls: list[dict] = []
    for cid, rows in by_co.items():
        all_calls += compress_calls(rows, price_by.get(cid))

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
        **agg,
        # Per-call ledger, newest first, worst-kept-in: full transparency.
        "calls": sorted(scored, key=lambda c: (c["start_date"], c["ticker"] or ""),
                        reverse=True),
    }
