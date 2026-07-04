"""
app/dhan/backfill.py — populate HistoricalPrice from Dhan daily OHLCV (REST).

The HistoricalPrice table is empty today (the IndianAPI ingester never filled
it), so the chart tab falls back to the 1-yr PricePoint series and the
momentum/low-vol factors run on a short window. Dhan's daily-from-inception
history fixes both. Raw closes are stored; the /history endpoint already
back-adjusts for splits/bonuses on read (Phase A), so no adjustment here.

Idempotent (skips dates already stored). Scoped/scheduled by the caller.
"""
from __future__ import annotations
import datetime as _dt
from collections import Counter

from . import client, instruments
from .. import models


def backfill_ticker(db, co, years: int = 5, days: int | None = None):
    """Fetch Dhan daily history for one company and upsert new dates into
    HistoricalPrice. Returns the number of rows added, or a status string.
    `days` overrides `years` with a short window — the daily top-up path
    (a couple of REST calls' worth of data instead of 5 years)."""
    sid = instruments.security_id(co.ticker)
    if not sid:
        return "no_security_id"
    to = _dt.date.today()
    frm = to - (_dt.timedelta(days=days) if days else _dt.timedelta(days=365 * years + 7))
    rows = client.historical_daily(sid, frm.isoformat(), to.isoformat())
    if rows is None:
        return "unconfigured"
    if not rows:
        return "no_data"
    existing = {r[0] for r in db.query(models.HistoricalPrice.date)
                .filter_by(company_id=co.id).all()}
    added = 0
    for r in rows:
        d, close = r.get("date"), r.get("close")
        if not d or close is None or d in existing:
            continue
        db.add(models.HistoricalPrice(company_id=co.id, date=d,
               open=r.get("open"), high=r.get("high"), low=r.get("low"),
               close=close, volume=r.get("volume")))
        added += 1
    db.commit()
    return added


def sync_snapshots_from_history(db, tickers) -> dict:
    """Mark MarketSnapshot to the latest stored Dhan close for names whose
    snapshot is missing or older than that close's date.

    This is what lets the visible universe scale past the IndianAPI quota:
    IndianAPI stays authoritative for the core set it refreshes daily (a
    same-day snapshot is never overwritten), and every other visible name is
    marked to Dhan's EOD close instead of drifting on a stale price."""
    import datetime as _dt2
    from sqlalchemy import func
    tset = {(t or "").upper() for t in tickers}
    cos = {c.id: c for c in db.query(models.Company).all()
           if (c.ticker or "").upper() in tset}
    if not cos:
        return {"updated": 0, "candidates": 0}
    latest = dict(db.query(models.HistoricalPrice.company_id,
                           func.max(models.HistoricalPrice.date))
                    .filter(models.HistoricalPrice.company_id.in_(list(cos)))
                    .group_by(models.HistoricalPrice.company_id).all())
    snap = {m.company_id: m for m in db.query(models.MarketSnapshot).all()}
    updated = 0
    for cid, d in latest.items():
        try:
            hist_date = _dt2.date.fromisoformat(str(d)[:10])
        except ValueError:
            continue
        hp = (db.query(models.HistoricalPrice)
                .filter_by(company_id=cid, date=d).first())
        if not hp or hp.close is None:
            continue
        # NSE close ≈ 15:30 IST = 10:00 UTC — an honest as_of for an EOD mark.
        as_of = _dt2.datetime.combine(hist_date, _dt2.time(10, 0))
        m = snap.get(cid)
        if m is None:
            db.add(models.MarketSnapshot(company_id=cid, price=hp.close, as_of=as_of))
            updated += 1
        else:
            m_date = m.as_of.date() if isinstance(m.as_of, _dt2.datetime) else None
            if m_date is None or m_date < hist_date:
                m.price, m.as_of = hp.close, as_of
                updated += 1
    db.commit()
    return {"updated": updated, "candidates": len(latest)}


def backfill_prices(db, tickers, years: int = 5, days: int | None = None) -> dict:
    """Backfill a set of tickers. Returns a status Counter as a dict.
    Pass `days` for the incremental daily top-up (see backfill_ticker)."""
    stats = Counter()
    cos = {(c.ticker or "").upper(): c for c in db.query(models.Company).all()}
    for t in tickers:
        co = cos.get((t or "").upper())
        if not co:
            stats["missing_company"] += 1
            continue
        res = backfill_ticker(db, co, years, days=days)
        if isinstance(res, int):
            stats["ok"] += 1
            stats["rows_added"] += res
        else:
            stats[res] += 1
        if stats.get("unconfigured"):        # no token → stop early, nothing will work
            break
    return dict(stats)
