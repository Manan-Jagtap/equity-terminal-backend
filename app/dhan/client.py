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
    """Active token: the self-renewing TOTP-minted token when auto-auth is
    configured (it can't silently expire), else the env DHAN_ACCESS_TOKEN.
    Auto comes FIRST deliberately — a stale manual token left in the env must
    not shadow a fresh auto one (that inversion caused the July 2026 outage
    to persist)."""
    from app.dhan import auth as _auth
    tok = _auth.auto_token() if _auth.enabled() else ""
    return tok or os.getenv("DHAN_ACCESS_TOKEN", "").strip()


def _client_id_from_token() -> str:
    """The Dhan access token is a JWT whose payload carries its own dhanClientId
    claim — decode it (no signature check needed; we only read our own token).
    This is the source of truth: it matches the token by construction."""
    tok = access_token()
    try:
        import base64
        import json
        body = tok.split(".")[1]
        body += "=" * (-len(body) % 4)
        claims = json.loads(base64.urlsafe_b64decode(body))
        return str(claims.get("dhanClientId") or "").strip()
    except Exception:
        return ""


def client_id() -> str:
    """Prefer the id embedded in the token (can't mismatch); DHAN_CLIENT_ID is
    only a fallback for tokens that lack the claim. A wrong env var caused a
    live option-chain 401 (Dhan error 808) — this makes that class impossible."""
    return _client_id_from_token() or os.getenv("DHAN_CLIENT_ID", "").strip()


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


# Dhan's documented 5 req/s proved optimistic in practice (a burst of ~3 rapid
# historical calls tripped a 429), so pace conservatively and make it tunable via
# env without a redeploy. The 429 RETRY below is the real safety net.
_data_rl = _RateLimiter(float(os.getenv("DHAN_MIN_INTERVAL", "0.6")))   # ~1.7 req/s
_oc_rl = _RateLimiter(3.1)      # 1 request / 3s — the Option Chain cap

_RETRYABLE = {429, 502, 503, 504}


def _post(path: str, body: dict, rl: _RateLimiter, extra_headers: dict | None = None,
          timeout: float = 20.0, retries: int = 5):
    """POST to Dhan with auth. None when unconfigured. Retries 429/5xx with
    exponential backoff (honouring Retry-After); raises on a non-retryable error
    or once retries are exhausted, so the caller can skip that item and continue."""
    tok = access_token()
    if not tok:
        return None
    headers = {"access-token": tok, "Content-Type": "application/json", "Accept": "application/json"}
    if extra_headers:
        headers.update({k: v for k, v in extra_headers.items() if v})
    resp = None
    auth_retried = False
    for attempt in range(retries):
        rl.wait()
        resp = httpx.post(BASE + path, headers=headers, json=body, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
        # Self-heal on auth failure: the auto-managed token may have been
        # rotated by the other service, or just expired — mint/reload once
        # and retry with the fresh one before giving up.
        if resp.status_code == 401 and not auth_retried:
            auth_retried = True
            from app.dhan import auth as _auth
            if _auth.enabled():
                _auth.invalidate(bad_token=tok)
                tok = access_token()
                if tok:
                    headers["access-token"] = tok
                    continue
        if resp.status_code in _RETRYABLE and attempt < retries - 1:
            ra = resp.headers.get("Retry-After")
            try:
                delay = float(ra) if ra else 0.0
            except ValueError:
                delay = 0.0
            time.sleep(delay if delay > 0 else min(10.0, 0.75 * (2 ** attempt)))
            continue
        resp.raise_for_status()     # non-retryable error → raise
        return resp.json()
    resp.raise_for_status()         # retries exhausted (persistent 429) → raise
    return None


# ── Historical daily OHLCV ───────────────────────────────────────────────────
def _f(arr, i):
    try:
        return float(arr[i])
    except Exception:
        return None


IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))


def rows_from_candles(data: dict) -> list[dict]:
    """Dhan returns parallel arrays (open/high/low/close/volume/timestamp). Zip
    them into [{date 'YYYY-MM-DD', open, high, low, close, volume}] oldest→newest.

    The candle date MUST be taken in IST, not UTC: Dhan stamps some responses at
    market hours (09:15 IST → same date either way) and some at midnight IST —
    which is 18:30 UTC of the PREVIOUS day, so a UTC conversion shifted every
    trading day back by one (Sunday-dated candles in prod, and duplicate-key
    crashes where the two conventions met). IST is correct for both."""
    if not isinstance(data, dict):
        return []
    o, h, l, c, v, t = (data.get(k) or [] for k in ("open", "high", "low", "close", "volume", "timestamp"))
    rows = []
    for i in range(len(t)):
        try:
            d = _dt.datetime.fromtimestamp(int(t[i]), IST).strftime("%Y-%m-%d")
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


# ── Batch LTP (Market Quote API) ─────────────────────────────────────────────
# One REST call prices up to 1000 instruments — the whole visible universe plus
# the headline indices in a single request. This is what makes near-real-time
# prices possible WITHOUT WebSockets (the recorder keeps its connection budget).
_quote_rl = _RateLimiter(1.1)      # Quote APIs: 1 request/second


def ltp_quote(id_map: dict) -> dict | None:
    """id_map: {"NSE_EQ": [securityIds...], "IDX_I": [...]} (ints or strs).
    Returns {"NSE_EQ": {"11536": 4520.5, ...}, "IDX_I": {...}} or None when
    unconfigured. Raises on HTTP errors (caller decides)."""
    body = {seg: [int(i) for i in ids] for seg, ids in id_map.items() if ids}
    if not body:
        return {}
    data = _post("/marketfeed/ltp", body, _quote_rl,
                 extra_headers={"client-id": client_id()})
    if data is None:
        return None
    out = {}
    for seg, quotes in ((data.get("data") or {}).items()):
        seg_out = {}
        for sid, q in (quotes or {}).items():
            px = (q or {}).get("last_price")
            if px is not None:
                try:
                    seg_out[str(sid)] = float(px)
                except (TypeError, ValueError):
                    pass
        out[seg] = seg_out
    return out


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
