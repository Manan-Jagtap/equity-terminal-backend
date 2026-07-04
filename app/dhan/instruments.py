"""
app/dhan/instruments.py — Dhan instrument master → {NSE ticker: securityId}.

Dhan addresses scrips by numeric securityId, not ticker, so we fetch the public
compact scrip-master CSV (no token needed) and build a ticker→id map for NSE
equities (and one for NSE indices, for index option chains). Cached in memory
with a ~daily refresh. The header is matched BY NAME (with fallbacks) so a minor
column change upstream doesn't silently break the map.

CSV: https://images.dhan.co/api-data/api-scrip-master.csv
"""
from __future__ import annotations
import csv
import io
import time

import httpx

CSV_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
_TTL = 3600 * 20   # refresh at most ~daily

# Candidate column names (upper-cased match against the CSV header).
_SEC = ("SEM_SMST_SECURITY_ID", "SECURITY_ID", "SEM_SECURITY_ID")
_SYM = ("SEM_TRADING_SYMBOL", "SM_SYMBOL_NAME", "SYMBOL_NAME", "SEM_CUSTOM_SYMBOL")
_EXCH = ("SEM_EXM_EXCH_ID", "EXCH_ID")
_SEG = ("SEM_SEGMENT", "SEGMENT")
_INSTR = ("SEM_INSTRUMENT_NAME", "INSTRUMENT")

_cache = {"eq": None, "idx": None, "ts": 0.0}


def _pick(header, cands):
    up = {h.strip().upper(): i for i, h in enumerate(header)}
    for c in cands:
        if c in up:
            return up[c]
    return None


def parse_scrip_master(csv_text: str):
    """Pure: parse the scrip-master CSV → ({eq ticker: id}, {index ticker: id}).
    Equities = NSE + segment E + instrument EQUITY; indices = NSE + INDEX."""
    reader = csv.reader(io.StringIO(csv_text))
    rows = list(reader)
    if len(rows) < 2:
        return {}, {}
    header = rows[0]
    ci_sec, ci_sym = _pick(header, _SEC), _pick(header, _SYM)
    ci_exch, ci_seg, ci_instr = _pick(header, _EXCH), _pick(header, _SEG), _pick(header, _INSTR)
    if ci_sec is None or ci_sym is None:
        return {}, {}
    eq, idx = {}, {}
    for row in rows[1:]:
        if len(row) <= max(ci_sec, ci_sym):
            continue
        sym = (row[ci_sym] or "").strip().upper()
        sec = (row[ci_sec] or "").strip()
        if not sym or not sec:
            continue
        exch = (row[ci_exch].strip().upper() if ci_exch is not None and len(row) > ci_exch else "")
        seg = (row[ci_seg].strip().upper() if ci_seg is not None and len(row) > ci_seg else "")
        instr = (row[ci_instr].strip().upper() if ci_instr is not None and len(row) > ci_instr else "")
        if exch and exch != "NSE":
            continue
        if instr in ("INDEX", "INDX") or seg == "I":
            idx.setdefault(sym, sec)
        elif seg in ("E", "") and instr in ("EQUITY", "ES", ""):
            eq.setdefault(sym, sec)
    return eq, idx


def _load(force: bool = False):
    if not force and _cache["eq"] is not None and (time.time() - _cache["ts"]) < _TTL:
        return
    try:
        r = httpx.get(CSV_URL, timeout=40.0, follow_redirects=True)
        r.raise_for_status()
        eq, idx = parse_scrip_master(r.text)
        if eq:
            _cache.update({"eq": eq, "idx": idx, "ts": time.time()})
    except Exception:
        # keep any previously-loaded map; a transient CSV failure isn't fatal
        pass


def security_id(ticker: str, index: bool = False):
    """NSE securityId (str) for a ticker, or None. Handles Dhan's '&'/'-' quirks
    by trying a couple of common ticker spellings."""
    _load()
    m = (_cache["idx"] if index else _cache["eq"]) or {}
    if not ticker:
        return None
    t = ticker.strip().upper()
    for cand in (t, t.replace("-", ""), t.replace("&", "")):
        if cand in m:
            return m[cand]
    return None


def coverage() -> dict:
    _load()
    return {"equities": len(_cache["eq"] or {}), "indices": len(_cache["idx"] or {}),
            "age_s": round(time.time() - _cache["ts"], 1) if _cache["ts"] else None}
