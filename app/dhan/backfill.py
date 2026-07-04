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


def backfill_ticker(db, co, years: int = 5):
    """Fetch Dhan daily history for one company and upsert new dates into
    HistoricalPrice. Returns the number of rows added, or a status string."""
    sid = instruments.security_id(co.ticker)
    if not sid:
        return "no_security_id"
    to = _dt.date.today()
    frm = to - _dt.timedelta(days=365 * years + 7)
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


def backfill_prices(db, tickers, years: int = 5) -> dict:
    """Backfill a set of tickers. Returns a status Counter as a dict."""
    stats = Counter()
    cos = {(c.ticker or "").upper(): c for c in db.query(models.Company).all()}
    for t in tickers:
        co = cos.get((t or "").upper())
        if not co:
            stats["missing_company"] += 1
            continue
        res = backfill_ticker(db, co, years)
        if isinstance(res, int):
            stats["ok"] += 1
            stats["rows_added"] += res
        else:
            stats[res] += 1
        if stats.get("unconfigured"):        # no token → stop early, nothing will work
            break
    return dict(stats)
