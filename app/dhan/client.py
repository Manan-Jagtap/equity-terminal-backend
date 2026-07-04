"""
app/dhan/client.py — DhanHQ v2 Data-API REST client (REST-ONLY, by design).

Deliberately REST-only: the terminal opens ZERO WebSocket connections, so it can
never touch Dhan's ~5-connection cap and can't disrupt a separate recorder
holding its own live feeds. It uses only the Data REST APIs:
  · POST /v2/charts/historical  — daily OHLCV (from inception), 5 req/s / 100k/day
  · POST /v2/optionchain        — full chain (OI/greeks/IV/bid-ask), 1 req/3s
  · POST /v2/optionchain/expirylist

Auth: DHAN_ACCESS_TOKEN (JWT, rotates ~daily) + DHAN_CLIENT_ID via env. The token
can be refreshed out-of-band (e.g. copied from the recorder's SSM/S3); a stale
token just fails the next call — no data loss (unlike a dropped WebSocket). When
the token is absent every call returns None, so the app is unaffected if Dhan
isn't configured.
"""
from __future__ import annotations
import os
import time
import threading
import datetime as _dt

import httpx

BASE = "https://api.dhan.co/v2"


def access_token() -> str:
    return os.getenv("DHAN_ACCESS_TOKEN", "").strip()


def client_id() -> str:
    return os.getenv("DHAN_CLIENT_ID", "").strip()


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
            gap = time.monotonic() - self._last
            if gap < self.min_interval:
                time.sleep(self.min_interval - gap)
            self._last = time.monotonic()


_data_rl = _RateLimiter(0.22)   # ~4.5 req/s — under the 5/s Data-API cap
_oc_rl = _RateLimiter(3.1)      # 1 request / 3s — the Option Chain cap


def _post(path: str, body: dict, rl: _RateLimiter, extra_headers: dict | None = None,
          timeout: float = 20.0):
    """POST to Dhan with auth; None when unconfigured. Raises on HTTP error so the
    caller can decide whether to swallow it."""
    tok = access_token()
    if not tok:
        return None
    rl.wait()
    headers = {"access-token": tok, "Content-Type": "application/json", "Accept": "application/json"}
    if extra_headers:
        headers.update({k: v for k, v in extra_headers.items() if v})
    r = httpx.post(BASE + path, headers=headers, json=body, timeout=timeout)
    r.raise_for_status()
    return r.json()


# ── Historical daily OHLCV ───────────────────────────────────────────────────
def _f(arr, i):
    try:
        return float(arr[i])
    except Exception:
        return None


def rows_from_candles(data: dict) -> list[dict]:
    """Dhan returns parallel arrays (open/high/low/close/volume/timestamp). Zip
    them into [{date 'YYYY-MM-DD', open, high, low, close, volume}] oldest→newest."""
    if not isinstance(data, dict):
        return []
    o, h, l, c, v, t = (data.get(k) or [] for k in ("open", "high", "low", "close", "volume", "timestamp"))
    rows = []
    for i in range(len(t)):
        try:
            d = _dt.datetime.utcfromtimestamp(int(t[i])).strftime("%Y-%m-%d")
        except Exception:
            continue
        rows.append({"date": d, "open": _f(o, i), "high": _f(h, i),
                     "low": _f(l, i), "close": _f(c, i), "volume": _f(v, i)})
    return rows


def historical_daily(security_id, from_date: str, to_date: str,
                     exchange_segment: str = "NSE_EQ", instrument: str = "EQUITY"):
    """Daily OHLCV rows for a security over [from_date, to_date). None when Dhan
    is unconfigured; [] when it returns nothing."""
    body = {"securityId": str(security_id), "exchangeSegment": exchange_segment,
            "instrument": instrument, "expiryCode": 0, "oi": False,
            "fromDate": from_date, "toDate": to_date}
    data = _post("/charts/historical", body, _data_rl)
    if data is None:
        return None
    return rows_from_candles(data)


# ── Option chain ─────────────────────────────────────────────────────────────
def _leg(x: dict) -> dict:
    x = x or {}
    g = x.get("greeks") or {}
    return {"ltp": x.get("last_price"), "oi": x.get("oi"), "volume": x.get("volume"),
            "iv": x.get("implied_volatility"),
            "delta": g.get("delta"), "theta": g.get("theta"),
            "gamma": g.get("gamma"), "vega": g.get("vega"),
            "bid": x.get("top_bid_price"), "ask": x.get("top_ask_price"),
            "prev_oi": x.get("previous_oi")}


def normalize_chain(data: dict) -> dict:
    """Flatten Dhan's {data:{last_price, oc:{strike:{ce,pe}}}} into a strike-sorted
    list + PCR + total OI. Pure."""
    d = (data or {}).get("data") or {}
    oc = d.get("oc") or {}
    strikes, ce_oi, pe_oi = [], 0.0, 0.0
    for strike_str in sorted(oc.keys(), key=lambda s: float(s)):
        sides = oc[strike_str] or {}
        ce, pe = sides.get("ce") or {}, sides.get("pe") or {}
        ce_oi += ce.get("oi") or 0
        pe_oi += pe.get("oi") or 0
        strikes.append({"strike": float(strike_str), "ce": _leg(ce), "pe": _leg(pe)})
    pcr = (pe_oi / ce_oi) if ce_oi else None
    return {"last_price": d.get("last_price"), "pcr": pcr,
            "total_ce_oi": ce_oi, "total_pe_oi": pe_oi, "strikes": strikes}


def expiry_list(underlying_security_id, seg: str = "NSE_EQ"):
    body = {"UnderlyingScrip": int(underlying_security_id), "UnderlyingSeg": seg}
    data = _post("/optionchain/expirylist", body, _oc_rl, extra_headers={"client-id": client_id()})
    if data is None:
        return None
    return data.get("data") or []


def option_chain(underlying_security_id, expiry: str, seg: str = "NSE_EQ"):
    body = {"UnderlyingScrip": int(underlying_security_id), "UnderlyingSeg": seg, "Expiry": expiry}
    data = _post("/optionchain", body, _oc_rl, extra_headers={"client-id": client_id()})
    if data is None:
        return None
    return normalize_chain(data)
