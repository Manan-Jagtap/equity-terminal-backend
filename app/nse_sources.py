"""
app/nse_sources.py — official NSE data feeds (public, cookie-gated JSON).

NSE serves JSON from www.nseindia.com/api/* but rejects header-less/cookieless
requests. A short homepage GET mints the session cookie the API then accepts —
the standard, polite pattern (one warm-up per refresh, generous timeouts).

Feeds wired here (all official, all free):
  · FII/DII daily cash-market flows  → macro store (feeds the regime block)
  · Insider trades (SEBI PIT via NSE) → per-ticker governance signal

Nothing here is on a hot path: a daily scheduler job refreshes both, and every
fetch fails silent so a rate-limit or markup change never breaks a request.
"""
from __future__ import annotations

import datetime as _dt
import logging

import requests

log = logging.getLogger("nse_sources")

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")
_BASE = "https://www.nseindia.com"
FIIDII_KEY = "fii_dii_flows_v1"
INSIDER_KEY = "insider_trades_v1"


def _session() -> requests.Session | None:
    s = requests.Session()
    s.headers.update({"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9",
                      "Accept": "application/json, text/plain, */*"})
    try:
        s.get(_BASE + "/", timeout=15)          # mint cookie (403 body is fine)
        return s
    except Exception as e:
        log.warning(f"NSE cookie warm-up failed: {type(e).__name__}: {e}")
        return None


def _get(s, path, referer):
    r = s.get(_BASE + path, timeout=20, headers={"Referer": _BASE + referer})
    if r.status_code != 200:
        return None
    return r.json()


def fetch_fii_dii(db) -> int:
    """FII/FPI + DII daily net cash-market flows → macro overlay series
    (fii_net_cr, dii_net_cr). Feeds the regime read: sustained FII selling is
    a genuine market-wide headwind our own price data can't see."""
    from app import macro_data
    s = _session()
    if not s:
        return 0
    data = _get(s, "/api/fiidiiTradeReact", "/reports-indices-historical-data")
    if not isinstance(data, list):
        return 0
    fii_pts, dii_pts = [], []
    today = _dt.date.today().isoformat()
    for row in data:
        if not isinstance(row, dict):
            continue
        cat = (row.get("category") or "").upper()
        net = row.get("netValue")
        d = row.get("date")
        try:
            iso = _dt.datetime.strptime(d, "%d-%b-%Y").date().isoformat()
        except (TypeError, ValueError):
            iso = today
        try:
            net = float(str(net).replace(",", ""))
        except (TypeError, ValueError):
            continue
        if "FII" in cat or "FPI" in cat:
            fii_pts.append([iso, net])
        elif "DII" in cat:
            dii_pts.append([iso, net])
    wrote = 0
    if fii_pts:
        wrote += macro_data.write_overlay(db, {"fii_net_cr": {
            "name": "FII/FPI net cash flow (₹ cr)", "freq": "D", "points": fii_pts}})
    if dii_pts:
        wrote += macro_data.write_overlay(db, {"dii_net_cr": {
            "name": "DII net cash flow (₹ cr)", "freq": "D", "points": dii_pts}})
    return wrote


def fetch_insider_trades(db, tickers: list[str], limit: int = 60) -> int:
    """Per-ticker insider (SEBI PIT) trades from NSE → KVStore insider_trades_v1
    ({ticker: {net_qty, n_buys, n_sells, last_date}}). A promoter/insider
    NET-SELL cluster is a governance signal the Fund Manager can weigh."""
    from app import models
    s = _session()
    if not s:
        return 0
    row = db.query(models.KVStore).filter_by(key=INSIDER_KEY).first()
    store = (row.value or {}).get("by_ticker", {}) if row else {}
    done = 0
    for tk in tickers[:limit]:
        data = _get(s, f"/api/corporates-pit?index=equities&symbol={tk}",
                    f"/get-quotes/equity?symbol={tk}")
        rows = (data or {}).get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            continue
        net = buys = sells = 0
        last = None
        for it in rows[:40]:
            if not isinstance(it, dict):
                continue
            mode = (it.get("acqMode") or it.get("tdpTransactionType") or "").lower()
            qty = it.get("secAcq") or it.get("secVal")
            try:
                qty = float(str(qty).replace(",", "")) if qty is not None else 0
            except (TypeError, ValueError):
                qty = 0
            is_sell = "disp" in mode or "sell" in mode or "pledge" in mode
            if is_sell:
                sells += 1
                net -= qty
            else:
                buys += 1
                net += qty
            last = it.get("date") or it.get("tdpDateOfAllotment") or last
        if buys or sells:
            store[tk] = {"net_qty": net, "n_buys": buys, "n_sells": sells,
                         "last_date": last}
            done += 1
    payload = {"by_ticker": store,
               "updated_at": _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"}
    if row:
        row.value = payload
    else:
        db.add(models.KVStore(key=INSIDER_KEY, value=payload))
    db.commit()
    return done


def insider_by_ticker(db) -> dict:
    from app import models
    row = db.query(models.KVStore).filter_by(key=INSIDER_KEY).first()
    return (row.value or {}).get("by_ticker", {}) if row else {}
