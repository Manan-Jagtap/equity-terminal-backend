"""
app/upstox/instruments.py — Upstox instrument master → {NSE ticker: instrument_key}.

Upstox addresses scrips by `instrument_key` ("NSE_EQ|INE002A01018" — an
ISIN-based string, not a number like Dhan's securityId), so this mirrors
app/dhan/instruments.py one-for-one but returns those strings. Same three maps:
equities, indices, and the set of F&O-UNDERLYING tickers (so the UI keeps
hiding the Options tab for cash-only names). Cached with a ~daily refresh.

Master: https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz
(public — no token needed, same as Dhan's scrip-master CSV).

⚠ Filter on `segment == "NSE_EQ"`, NEVER on `instrument_type == "EQ"`. The
master files REITs/InvITs as RR/IV and the trade-for-trade / surveillance names
as BE/BZ. Filtering by instrument_type silently drops 23 live names (EMBASSY,
MINDSPACE, BIRET, CUBEINVIT, BAGMANE, HEG, HFCL, RELINFRA, STLTECH, …) — they
resolve fine, they just aren't type "EQ". Measured against the live universe:
segment filter = 1034/1035 tickers, type filter = 1011/1035.
"""
from __future__ import annotations
import gzip
import io
import json
import time

import httpx

MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
_TTL = 3600 * 20   # refresh at most ~daily

_cache = {"eq": None, "idx": None, "fno": None, "ts": 0.0}


def parse_master(rows: list) -> tuple[dict, dict, set]:
    """Pure: instrument-master rows → ({eq ticker: instrument_key},
    {index name/symbol: instrument_key}, {F&O-underlying tickers}).

    Indices are keyed by BOTH `name` and `trading_symbol` because the two
    differ for the headline ones (name "Nifty Fin Service" vs symbol
    "FINNIFTY"), and callers pass either spelling."""
    eq, idx, fno = {}, {}, set()
    if not isinstance(rows, list):
        return eq, idx, fno
    for r in rows:
        if not isinstance(r, dict):
            continue
        seg = (r.get("segment") or "").strip().upper()
        key = (r.get("instrument_key") or "").strip()
        sym = (r.get("trading_symbol") or "").strip().upper()
        if not key:
            continue
        if seg == "NSE_EQ":
            if sym:
                eq.setdefault(sym, key)
        elif seg == "NSE_INDEX":
            name = (r.get("name") or "").strip().upper()
            if sym:
                idx.setdefault(sym, key)
            if name:
                idx.setdefault(name, key)
        elif seg == "NSE_FO":
            if (r.get("underlying_type") or "").strip().upper() == "EQUITY":
                under = (r.get("underlying_symbol") or "").strip().upper()
                if under:
                    fno.add(under)
    # A real stock-option underlying must itself be a listed NSE equity.
    fno &= set(eq)
    return eq, idx, fno


def _load(force: bool = False):
    if not force and _cache["eq"] is not None and (time.time() - _cache["ts"]) < _TTL:
        return
    try:
        r = httpx.get(MASTER_URL, timeout=60.0, follow_redirects=True)
        r.raise_for_status()
        rows = json.load(gzip.GzipFile(fileobj=io.BytesIO(r.content)))
        eq, idx, fno = parse_master(rows)
        if eq:
            _cache.update({"eq": eq, "idx": idx, "fno": fno, "ts": time.time()})
    except Exception:
        # keep any previously-loaded map; a transient fetch failure isn't fatal
        pass


def security_id(ticker: str, index: bool = False):
    """NSE instrument_key for a ticker, or None. Tries a couple of common
    ticker spellings, matching the Dhan module's '&'/'-' tolerance."""
    _load()
    m = (_cache["idx"] if index else _cache["eq"]) or {}
    if not ticker:
        return None
    t = ticker.strip().upper()
    for cand in (t, t.replace("-", ""), t.replace("&", "")):
        if cand in m:
            return m[cand]
    return None


def fno_tickers() -> set:
    """Tickers with listed stock futures/options. Empty set when the master
    hasn't loaded (callers should fail OPEN rather than hide everything)."""
    _load()
    return _cache["fno"] or set()


# Dashboard display names → Upstox index spellings. Most resolve by exact
# `name` match; only the derivative-style ones need an alias.
_INDEX_ALIASES = {
    "NIFTY 50": "NIFTY 50", "NIFTY BANK": "NIFTY BANK", "BANK NIFTY": "NIFTY BANK",
    "NIFTY FINANCIAL SERVICES": "FINNIFTY", "NIFTY FIN SERVICE": "FINNIFTY",
    "NIFTY NEXT 50": "NIFTY NEXT 50", "NIFTY IT": "NIFTY IT",
    "NIFTY MIDCAP SELECT": "MIDCPNIFTY", "NIFTY MIDCAP 100": "NIFTY MIDCAP 100",
    "INDIA VIX": "INDIA VIX",
}


def resolve_index(name, idx) -> str | None:
    """Pure: display name → instrument_key using aliases, exact, space-insensitive
    and (longest-key-first) containment matching. None when unresolvable —
    e.g. SENSEX, a BSE index the NSE master doesn't carry."""
    if not name or not idx:
        return None
    n = " ".join(name.strip().upper().split())
    alias = _INDEX_ALIASES.get(n)
    if alias and alias in idx:
        return idx[alias]
    if n in idx:
        return idx[n]
    ns = n.replace(" ", "")
    for k, v in idx.items():
        if k.replace(" ", "") == ns:
            return v
    for k in sorted(idx, key=len, reverse=True):      # longest first: "NIFTY" can't
        kns = k.replace(" ", "")                       # swallow "NIFTY METAL"
        if len(kns) >= 6 and (kns in ns or ns in kns):
            return idx[k]
    return None


def index_security_id(name) -> str | None:
    _load()
    return resolve_index(name, _cache["idx"] or {})


def coverage() -> dict:
    _load()
    return {"equities": len(_cache["eq"] or {}), "indices": len(_cache["idx"] or {}),
            "fno_underlyings": len(_cache["fno"] or set()),
            "age_s": round(time.time() - _cache["ts"], 1) if _cache["ts"] else None}
