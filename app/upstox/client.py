"""
app/upstox/client.py — Upstox Data-API REST client (REST-ONLY, by design).

A drop-in replacement for app/dhan/client.py: same function names, same
argument order, same return shapes, so the call sites only change which module
they import (see app/market_data.py). Endpoints used:
  · GET /v2/market-quote/ltp          — batch LTP, 500 instruments per call
  · GET /v3/historical-candle/…       — daily OHLCV (days from 2000)
  · GET /v2/option/contract           — expiry list
  · GET /v2/option/chain              — full chain (OI/greeks/IV/bid-ask)

Auth: a single UPSTOX_ACCESS_TOKEN ("Analytics" token, read-only, 1-year
validity). Unlike Dhan's 24h token there is NO TOTP mint/refresh cycle to run —
which is why the whole self-renewing machinery in app/dhan/auth.py has no
counterpart here. When the token is absent every call returns None, so the app
degrades exactly as it did when Dhan was unconfigured.

Batch size is 500, NOT 1000: the LTP endpoint is a GET and 1000 instrument_keys
overflow the URL (HTTP 414). ltp_quote() chunks transparently, so the caller can
still hand it the whole universe in one call as it did with Dhan.
"""
from __future__ import annotations
import os
import time
import threading
import datetime as _dt
import urllib.parse

import httpx

BASE = "https://api.upstox.com"
IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))

# The LTP endpoint is a GET; 1000 keys exceed the URL limit (verified: HTTP 414).
LTP_BATCH = 500


def access_token() -> str:
    return os.getenv("UPSTOX_ACCESS_TOKEN", "").strip()


def client_id() -> str:
    """Interface parity with the Dhan client (which needs a client-id header).
    Upstox carries identity inside the bearer token, so this is unused."""
    return ""


def configured() -> bool:
    return bool(access_token())


# ── Self-imposed rate limits (per process, thread-safe) ──────────────────────
class _RateLimiter:
    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self):
        with self._lock:
            gap = time.time() - self._last
            if gap < self.min_interval:
                time.sleep(self.min_interval - gap)
            self._last = time.time()


_quote_rl = _RateLimiter(0.4)
_data_rl = _RateLimiter(0.4)
_oc_rl = _RateLimiter(1.1)      # option chain is the heaviest call


def _get(path: str, params: dict | None, rl: _RateLimiter, tries: int = 3):
    """GET with the bearer token, self-imposed rate limit and 429 backoff.
    Returns the parsed JSON body, or None when unconfigured."""
    tok = access_token()
    if not tok:
        return None
    headers = {"Authorization": f"Bearer {tok}", "Accept": "application/json"}
    resp = None
    for attempt in range(tries):
        rl.wait()
        resp = httpx.get(f"{BASE}{path}", params=params, headers=headers,
                         timeout=40.0, follow_redirects=True)
        if resp.status_code == 429:
            time.sleep(1.5 * (attempt + 1))
            continue
        resp.raise_for_status()
        return resp.json()
    if resp is not None:
        resp.raise_for_status()     # retries exhausted (persistent 429) → raise
    return None


# ── Batch LTP ────────────────────────────────────────────────────────────────
def ltp_quote(id_map: dict) -> dict | None:
    """id_map: {"NSE_EQ": [instrument_keys...], "IDX_I": [...]}.
    Returns {"NSE_EQ": {"NSE_EQ|INE002A01018": 1309.5, ...}, "IDX_I": {...}}
    keyed by the SAME instrument_key the caller passed in, or None when
    unconfigured. Chunks internally at 500/request."""
    if not configured():
        return None
    wanted = {seg: [str(i) for i in ids if i] for seg, ids in (id_map or {}).items() if ids}
    if not wanted:
        return {}

    # One flat de-duplicated request set; map results back per segment after.
    all_keys, seen = [], set()
    for ids in wanted.values():
        for k in ids:
            if k not in seen:
                seen.add(k)
                all_keys.append(k)

    prices: dict[str, float] = {}
    for i in range(0, len(all_keys), LTP_BATCH):
        chunk = all_keys[i:i + LTP_BATCH]
        data = _get("/v2/market-quote/ltp", {"instrument_key": ",".join(chunk)}, _quote_rl)
        if data is None:
            return None
        for q in ((data.get("data") or {}).values()):
            key = (q or {}).get("instrument_token")
            px = (q or {}).get("last_price")
            if key is None or px is None:
                continue
            try:
                prices[str(key)] = float(px)
            except (TypeError, ValueError):
                pass

    return {seg: {k: prices[k] for k in ids if k in prices} for seg, ids in wanted.items()}


# ── Historical daily OHLCV ───────────────────────────────────────────────────
def _f(v):
    try:
        return float(v)
    except Exception:
        return None


def rows_from_candles(data: dict) -> list[dict]:
    """Upstox returns data.candles = [[iso_ts, o, h, l, c, volume, oi], …]
    NEWEST-first. Flip to oldest→newest and shape like the Dhan client's rows.

    The timestamp is already IST-offset ("2026-09-04T00:00:00+05:30"), so the
    date is taken from the string's own offset — never re-interpreted in UTC,
    which is the bug that produced Sunday-dated candles on the Dhan path."""
    if not isinstance(data, dict):
        return []
    candles = ((data.get("data") or {}).get("candles")) or []
    rows = []
    for c in candles:
        if not isinstance(c, (list, tuple)) or len(c) < 6:
            continue
        try:
            d = _dt.datetime.fromisoformat(str(c[0])).strftime("%Y-%m-%d")
        except Exception:
            continue
        rows.append({"date": d, "open": _f(c[1]), "high": _f(c[2]),
                     "low": _f(c[3]), "close": _f(c[4]), "volume": _f(c[5])})
    rows.reverse()          # oldest → newest, matching the Dhan contract
    return rows


def historical_daily(security_id, from_date: str, to_date: str,
                     exchange_segment: str = "NSE_EQ", instrument: str = "EQUITY"):
    """Daily OHLCV rows for an instrument_key over [from_date, to_date]. None
    when unconfigured; [] when Upstox returns nothing. `exchange_segment` and
    `instrument` are accepted for signature parity with the Dhan client and are
    unused (the instrument_key already encodes the segment)."""
    key = urllib.parse.quote(str(security_id), safe="")
    data = _get(f"/v3/historical-candle/{key}/days/1/{to_date}/{from_date}", None, _data_rl)
    if data is None:
        return None
    return rows_from_candles(data)


# ── Option chain ─────────────────────────────────────────────────────────────
def _leg(x: dict) -> dict:
    """One side of a strike, flattened to the Dhan client's leg shape."""
    x = x or {}
    md = x.get("market_data") or {}
    g = x.get("option_greeks") or {}
    return {"ltp": md.get("ltp"), "oi": md.get("oi"), "volume": md.get("volume"),
            "iv": g.get("iv"),
            "delta": g.get("delta"), "theta": g.get("theta"),
            "gamma": g.get("gamma"), "vega": g.get("vega"),
            "bid": md.get("bid_price"), "ask": md.get("ask_price"),
            "prev_oi": md.get("prev_oi")}


def normalize_chain(data: dict) -> dict:
    """Flatten Upstox's per-strike list into the same strike-sorted structure
    the Dhan client produced (so the route and UI are unchanged). Pure.

    PCR is recomputed from total OI rather than trusting the per-row `pcr`
    field, which Upstox reports per strike (and leaves 0.0 on empty strikes)."""
    rows = (data or {}).get("data") or []
    strikes, ce_oi, pe_oi, spot = [], 0.0, 0.0, None
    for r in rows:
        if not isinstance(r, dict):
            continue
        if spot is None:
            spot = r.get("underlying_spot_price")
        ce, pe = r.get("call_options") or {}, r.get("put_options") or {}
        ce_oi += ((ce.get("market_data") or {}).get("oi")) or 0
        pe_oi += ((pe.get("market_data") or {}).get("oi")) or 0
        try:
            strike = float(r.get("strike_price"))
        except (TypeError, ValueError):
            continue
        strikes.append({"strike": strike, "ce": _leg(ce), "pe": _leg(pe)})
    strikes.sort(key=lambda s: s["strike"])
    pcr = (pe_oi / ce_oi) if ce_oi else None
    return {"last_price": spot, "pcr": pcr,
            "total_ce_oi": ce_oi, "total_pe_oi": pe_oi, "strikes": strikes}


def expiry_list(underlying_security_id, seg: str = "NSE_EQ"):
    """Sorted unique expiry dates ('YYYY-MM-DD') for an underlying instrument_key.
    `seg` is accepted for parity with the Dhan client and is unused."""
    data = _get("/v2/option/contract", {"instrument_key": str(underlying_security_id)}, _oc_rl)
    if data is None:
        return None
    out = set()
    for r in (data.get("data") or []):
        e = (r or {}).get("expiry")
        if e:
            out.add(str(e)[:10])
    return sorted(out)


def option_chain(underlying_security_id, expiry: str, seg: str = "NSE_EQ"):
    """Normalized option chain for an underlying instrument_key at `expiry`."""
    data = _get("/v2/option/chain",
                {"instrument_key": str(underlying_security_id), "expiry_date": expiry},
                _oc_rl)
    if data is None:
        return None
    return normalize_chain(data)


# ── Holdings (owner's own long-term positions) ───────────────────────────────
def holdings() -> list[dict] | None:
    """The account's long-term holdings as [{symbol, qty, avg_cost}], or None
    when unconfigured. Normalized so the route is vendor-agnostic.

    NOTE: Upstox gates Portfolio endpoints on a STATIC IP registered against the
    app (Upstox → My Apps → "+ Static IPs"). Without it this returns HTTP 401
    `UDAPI1221`; the caller turns that into a 503 with the remedy, because it is
    a configuration gap rather than a transient outage."""
    data = _get("/v2/portfolio/long-term-holdings", None, _data_rl)
    if data is None:
        return None
    out = []
    for h in (data.get("data") or []):
        sym = str((h or {}).get("trading_symbol") or "").upper().strip()
        qty = (h or {}).get("quantity") or 0
        avg = (h or {}).get("average_price") or 0
        if sym and qty and avg:
            out.append({"symbol": sym, "qty": qty, "avg_cost": avg})
    return out
