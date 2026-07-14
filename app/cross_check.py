"""
app/cross_check.py — second-source price cross-check (Dhan vs IndianAPI).

Both vendors already land in the DB (HistoricalPrice ← Dhan backfill + daily
top-up; MarketSnapshot ← IndianAPI EOD/intraday), so the check costs ZERO API
calls. It catches the two failure modes a single-vendor pipeline is blind to:

  STALE_HISTORY  — the Dhan series stopped updating (token died, backfill broke)
  STALE_SNAPSHOT — the IndianAPI live price stopped updating (quota, outage)
  DIVERGENT      — the vendors disagree on the close: a stale tick, a missed
                   split/bonus adjustment, or plain bad data — the Bajaj-Finance
                   class of bug, now caught automatically instead of by eye.

check_row() is pure (unit-tested without a DB); assemble_rows() does the reads.
"""
from __future__ import annotations
import datetime as _dt

from . import models

# Calendar-day staleness allowances (generous enough for weekends + holidays).
HIST_STALE_DAYS = 7
SNAP_STALE_DAYS = 4
# Vendors are compared only when both are fresh. A close-vs-live gap beyond WARN
# is suspicious; beyond ALERT it is split-shaped (≈50% for a 1:1 bonus).
DIVERGE_WARN = 0.06
DIVERGE_ALERT = 0.20


def check_row(row: dict, today: _dt.date | None = None) -> dict:
    """row: {ticker, snapshot_price, snapshot_date (date|None),
             hist_close, hist_date (date|None)} → status + flags."""
    today = today or _dt.date.today()
    flags: list[dict] = []

    hist_age = (today - row["hist_date"]).days if row.get("hist_date") else None
    snap_age = (today - row["snapshot_date"]).days if row.get("snapshot_date") else None

    if row.get("hist_close") is None:
        flags.append({"code": "NO_HISTORY", "level": "warn",
                      "message": "No Dhan price history stored — single-source name"})
    elif hist_age is not None and hist_age > HIST_STALE_DAYS:
        flags.append({"code": "STALE_HISTORY", "level": "warn",
                      "message": f"Dhan history last updated {hist_age}d ago"})

    if row.get("snapshot_price") is None:
        flags.append({"code": "NO_SNAPSHOT", "level": "alert",
                      "message": "No live price snapshot — valuation MoS is not meaningful"})
    elif snap_age is not None and snap_age > SNAP_STALE_DAYS:
        flags.append({"code": "STALE_SNAPSHOT", "level": "alert",
                      "message": f"Live price is {snap_age}d old — screener marks are stale"})

    gap = None
    # Only compare prices from the SAME trading day. During market hours the
    # live snapshot is today's price while the Dhan history still holds
    # yesterday's close (the EOD backfill catches up after the bell) — comparing
    # those two just measures today's move, not a vendor divergence. Requiring
    # snapshot_date == hist_date removes that whole class of false positives
    # while still catching genuine same-day, split-shaped mismatches.
    same_day = (row.get("snapshot_date") and row.get("hist_date")
                and row["snapshot_date"] == row["hist_date"])
    both_fresh = (row.get("hist_close") and row.get("snapshot_price")
                  and hist_age is not None and hist_age <= HIST_STALE_DAYS
                  and snap_age is not None and snap_age <= SNAP_STALE_DAYS
                  and same_day)
    if both_fresh:
        gap = row["snapshot_price"] / row["hist_close"] - 1.0
        if abs(gap) > DIVERGE_ALERT:
            flags.append({"code": "DIVERGENT", "level": "alert",
                          "message": (f"Vendors disagree {gap*100:+.1f}% — split-shaped; "
                                      "check corporate-action adjustment")})
        elif abs(gap) > DIVERGE_WARN:
            flags.append({"code": "DIVERGENT", "level": "warn",
                          "message": f"Dhan close vs live price gap {gap*100:+.1f}%"})

    status = ("alert" if any(f["level"] == "alert" for f in flags)
              else "warn" if flags else "ok")
    return {"ticker": row.get("ticker"), "status": status, "flags": flags,
            "gap_pct": gap,
            "snapshot_price": row.get("snapshot_price"),
            "hist_close": row.get("hist_close"),
            "hist_date": row["hist_date"].isoformat() if row.get("hist_date") else None,
            "snapshot_date": row["snapshot_date"].isoformat() if row.get("snapshot_date") else None}


def _to_date(v) -> _dt.date | None:
    if v is None:
        return None
    if isinstance(v, _dt.datetime):
        return v.date()
    if isinstance(v, _dt.date):
        return v
    try:
        return _dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def assemble_rows(db, tickers) -> list[dict]:
    """One row per ticker with the latest close each vendor holds."""
    from sqlalchemy import func
    tset = {(t or "").upper() for t in tickers}
    cos = [c for c in db.query(models.Company).all() if (c.ticker or "").upper() in tset]
    snap = {m.company_id: m for m in db.query(models.MarketSnapshot).all()}

    latest = dict(db.query(models.HistoricalPrice.company_id,
                           func.max(models.HistoricalPrice.date))
                    .group_by(models.HistoricalPrice.company_id).all())
    close_by = {}
    if latest:
        for hp in (db.query(models.HistoricalPrice)
                     .filter(models.HistoricalPrice.date.in_(set(latest.values()))).all()):
            if latest.get(hp.company_id) == hp.date:
                close_by[hp.company_id] = hp

    rows = []
    for co in cos:
        m, hp = snap.get(co.id), close_by.get(co.id)
        rows.append({"ticker": (co.ticker or "").upper(),
                     "snapshot_price": m.price if m else None,
                     "snapshot_date": _to_date(m.as_of) if m else None,
                     "hist_close": hp.close if hp else None,
                     "hist_date": _to_date(hp.date) if hp else None})
    return rows


def cross_check_universe(db, tickers, today: _dt.date | None = None) -> dict:
    checked = [check_row(r, today) for r in assemble_rows(db, tickers)]
    checked.sort(key=lambda r: ({"alert": 0, "warn": 1, "ok": 2}[r["status"]], r["ticker"] or ""))
    n_alert = sum(1 for r in checked if r["status"] == "alert")
    n_warn = sum(1 for r in checked if r["status"] == "warn")
    return {"as_of": (today or _dt.date.today()).isoformat(),
            "count": len(checked), "alerts": n_alert, "warnings": n_warn,
            "ok": len(checked) - n_alert - n_warn,
            "flagged": [r for r in checked if r["status"] != "ok"]}
