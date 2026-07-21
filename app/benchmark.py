"""benchmark.py — one honest NIFTY-50 benchmark, shared by the portfolio
alpha block, the model-trust calibration, and the engine ledger.

Two honesty points baked in (FIX-22 / ENG-09):
  · The Dhan feed is the NIFTY-50 PRICE index. Grading a dividend-inclusive
    portfolio against a price index overstates alpha, so `nifty_tr_return`
    accrues an approximate index dividend yield to make it a TRI *proxy* —
    labelled a proxy everywhere, never passed off as the NSE NIFTY-50 TRI.
  · Every function degrades to None when the feed is unavailable, so a caller
    simply shows no benchmark rather than a fabricated one.
"""
import bisect
import time as _t
import datetime as _dt

# NIFTY-50 trailing dividend yield ≈ 1.3%/yr (long-run ~1.2–1.5%). Used only to
# turn the price index into a TRI proxy; conservative on the low side so the
# benchmark is never flattered and the model's alpha is never overstated.
NIFTY_DIV_YIELD = 0.013

_CACHE = {"ts": 0.0, "data": None}


def nifty_series():
    """(dates, closes) for the NIFTY-50 from Dhan, cached 1h. None when the feed
    is unavailable."""
    if _CACHE["data"] is not None and _t.time() - _CACHE["ts"] < 3600:
        return _CACHE["data"]
    out = None
    try:
        from app.dhan import client, instruments
        if client.configured():
            sid = instruments.index_security_id("NIFTY 50")
            if sid:
                frm = (_dt.date.today() - _dt.timedelta(days=365 * 6)).isoformat()
                rows = client.historical_daily(sid, frm, _dt.date.today().isoformat(),
                                               exchange_segment="IDX_I", instrument="INDEX")
                pts = sorted((r["date"], r["close"]) for r in (rows or [])
                             if r.get("date") and r.get("close"))
                if len(pts) >= 30:
                    out = ([d for d, _ in pts], [c for _, c in pts])
    except Exception:
        out = None
    _CACHE["data"], _CACHE["ts"] = out, _t.time()
    return out


def _asof(dates, closes, d_iso):
    i = bisect.bisect_right(dates, d_iso) - 1
    return closes[i] if i >= 0 else None


def nifty_return(d0: str, d1: str | None = None):
    """NIFTY-50 PRICE return between two ISO dates (d1 defaults to latest). None
    when the feed is missing or d0 predates the series."""
    s = nifty_series()
    if not s:
        return None
    dates, closes = s
    p0 = _asof(dates, closes, d0)
    p1 = closes[-1] if d1 is None else _asof(dates, closes, d1)
    if not p0 or not p1 or p0 <= 0:
        return None
    return p1 / p0 - 1.0


def nifty_tr_return(d0: str, d1: str | None = None):
    """NIFTY-50 TOTAL-return PROXY: the price return plus an accrued approximate
    dividend yield over the span. Apples-to-apples against a dividend-inclusive
    portfolio (ENG-09). None when the price return is unavailable."""
    pr = nifty_return(d0, d1)
    if pr is None:
        return None
    s = nifty_series()
    dates = s[0]
    end = dates[-1] if d1 is None else d1
    try:
        years = max(0.0, (_dt.date.fromisoformat(end[:10]) - _dt.date.fromisoformat(d0[:10])).days / 365.0)
    except Exception:
        years = 0.0
    return pr + NIFTY_DIV_YIELD * years
