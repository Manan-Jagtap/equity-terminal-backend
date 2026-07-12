"""
app/live_prices.py — near-real-time prices for the whole terminal.

One Dhan batch-LTP call prices the entire visible universe + headline indices
(up to 1000 instruments per request). The route caches for ~12s during NSE
market hours, so any number of connected clients share ONE upstream call every
cache window — real-time-feeling prices with zero WebSockets (the recorder's
connection budget stays untouched) and zero IndianAPI quota.

Outside market hours the cache stretches and `live` reads false, so the
frontend can show EOD instead of a fake pulse.
"""
from __future__ import annotations
import datetime as _dt
import threading
import time

from .dhan import client, instruments

# Headline indices on the live ticker (display name → resolver input).
INDEX_NAMES = ["NIFTY 50", "NIFTY Bank", "NIFTY FINANCIAL SERVICES",
               "NIFTY Next 50", "NIFTY Midcap 100", "India VIX"]

_TTL_OPEN = 12.0          # one upstream call per 12s while the market trades
_TTL_CLOSED = 300.0

_lock = threading.Lock()
_cache: dict = {"ts": 0.0, "data": None}

IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))


def market_open(now: _dt.datetime | None = None) -> bool:
    """NSE cash session: 09:15–15:30 IST, Mon–Fri. (Holidays read as 'open'
    but prices simply don't move — harmless.)"""
    now = now or _dt.datetime.now(IST)
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    return (9 * 60 + 15) <= hm <= (15 * 60 + 30)


def _instrument_maps():
    """(eq_ids {ticker: sid}, idx_ids {display_name: sid}) for the visible set."""
    from .ingest.indianapi_ingester import VISIBLE_UNIVERSE
    eq = {}
    for t in VISIBLE_UNIVERSE:
        sid = instruments.security_id(t)
        if sid:
            eq[t] = str(sid)
    idx = {}
    for name in INDEX_NAMES:
        sid = instruments.index_security_id(name)
        if sid:
            idx[name] = str(sid)
    return eq, idx


def snapshot() -> dict:
    """The cached live-price payload. Never raises — on any upstream failure the
    previous payload is served (or an honest empty one)."""
    now = time.time()
    is_open = market_open()
    ttl = _TTL_OPEN if is_open else _TTL_CLOSED
    with _lock:
        if _cache["data"] is not None and now - _cache["ts"] < ttl:
            return _cache["data"]
    if not client.configured():
        return {"available": False, "live": False, "prices": {}, "indices": {},
                "message": "Live prices need the Dhan feed."}
    try:
        eq, idx = _instrument_maps()
        quotes = client.ltp_quote({"NSE_EQ": list(eq.values()),
                                   "IDX_I": list(idx.values())}) or {}
        eq_q = quotes.get("NSE_EQ") or {}
        idx_q = quotes.get("IDX_I") or {}
        data = {
            "available": True,
            "live": is_open,
            "as_of": _dt.datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST"),
            "prices": {t: eq_q[sid] for t, sid in eq.items() if sid in eq_q},
            "indices": {n: idx_q[sid] for n, sid in idx.items() if sid in idx_q},
        }
        with _lock:
            _cache.update(ts=now, data=data)
        return data
    except Exception:
        with _lock:
            if _cache["data"] is not None:
                return _cache["data"]
        return {"available": False, "live": False, "prices": {}, "indices": {},
                "message": "Live feed temporarily unavailable."}


def update_snapshots_from_live(db) -> int:
    """Scheduler path: mark MarketSnapshot for every visible name from ONE batch
    LTP call (replaces the 50-name IndianAPI intraday poll — ~4,400 fewer
    IndianAPI calls per month). Returns the number of snapshots updated."""
    from . import models
    if not client.configured():
        return 0
    data = snapshot()
    prices = data.get("prices") or {}
    if not prices:
        return 0
    cos = {(c.ticker or "").upper(): c.id for c in db.query(models.Company).all()}
    snaps = {m.company_id: m for m in db.query(models.MarketSnapshot).all()}
    now = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
    n = 0
    for t, px in prices.items():
        cid = cos.get(t)
        if not cid or not px or px <= 0:
            continue
        m = snaps.get(cid)
        if m is None:
            db.add(models.MarketSnapshot(company_id=cid, price=round(px, 2), as_of=now))
        else:
            m.price, m.as_of = round(px, 2), now
        n += 1
    db.commit()
    return n
