"""
app/strategy_backtest.py — a real, point-in-time strategy backtester.

Simulates rule-based PRICE strategies over the 5-year Dhan HistoricalPrice
series (already vendor split/bonus-adjusted), with the NIFTY 50 as the buy-hold
benchmark. Every signal at a rebalance date is computed from data available UP
TO that date only — no look-ahead, no survivorship editing, equal-weight books.

Honesty boundary: this backtests PRICE/technical factors (momentum, low-vol,
trend, 52-week strength, mean-reversion) because we have 5 years of daily prices
but NOT 5 years of point-in-time fundamentals. Value/quality strategies would
need historical financials we don't store, so they are deliberately NOT offered
here — the forward verdict-call track record (/api/backtest) and the factor
snapshot ledger (/api/factors/backtest) cover the fundamental side honestly.

A research/education tool. Past simulated performance is not indicative of
future results and is not investment advice.
"""
from __future__ import annotations
import bisect
import datetime as _dt
from statistics import mean, pstdev


# strategy id → (label, one-line rule, higher-is-better)
STRATEGIES = {
    "momentum":  ("Momentum",         "Hold the strongest 12-minus-1-month price trend.", True),
    "low_vol":   ("Low Volatility",   "Hold the steadiest names — lowest 12-month realised volatility.", True),
    "trend":     ("Trend (200-DMA)",  "Hold names trading furthest above their 200-day moving average.", True),
    "high52":    ("52-Week Strength", "Hold names closest to their 52-week high.", True),
    "reversion": ("Mean Reversion",   "Contrarian — hold the most oversold names (lowest RSI-14).", True),
}
_REBAL = {"M": ("Monthly", 12), "Q": ("Quarterly", 4)}
_RF = 0.065          # annual risk-free for the Sharpe ratio (India ~10-yr G-sec)
_MIN_HISTORY = 252   # trading days a name needs before it is eligible


def _asof(dates, closes, target):
    """Last close on or before `target` (ISO string). None if the series starts
    after target."""
    i = bisect.bisect_right(dates, target) - 1
    return closes[i] if i >= 0 else None


def _sig_momentum(dates, closes):
    if len(closes) < _MIN_HISTORY:
        return None
    return closes[-21] / closes[-252] - 1.0 if closes[-252] else None


def _sig_low_vol(dates, closes):
    if len(closes) < _MIN_HISTORY:
        return None
    window = closes[-252:]
    rets = [window[i] / window[i - 1] - 1.0 for i in range(1, len(window)) if window[i - 1]]
    sd = pstdev(rets) if len(rets) > 5 else None
    return (-sd) if sd is not None else None            # lower vol → higher score


def _sig_trend(dates, closes):
    if len(closes) < 200:
        return None
    sma200 = mean(closes[-200:])
    return closes[-1] / sma200 - 1.0 if sma200 else None


def _sig_high52(dates, closes):
    if len(closes) < _MIN_HISTORY:
        return None
    hi = max(closes[-252:])
    return closes[-1] / hi if hi else None


def _sig_reversion(dates, closes):
    if len(closes) < 30:
        return None
    window = closes[-15:]
    gains = losses = 0.0
    for i in range(1, len(window)):
        ch = window[i] - window[i - 1]
        if ch >= 0:
            gains += ch
        else:
            losses -= ch
    n = len(window) - 1
    avg_g, avg_l = gains / n, losses / n
    if avg_l == 0:
        rsi = 100.0
    else:
        rs = avg_g / avg_l
        rsi = 100.0 - 100.0 / (1.0 + rs)
    return -rsi                                          # most oversold → highest score


_SIGNALS = {"momentum": _sig_momentum, "low_vol": _sig_low_vol, "trend": _sig_trend,
            "high52": _sig_high52, "reversion": _sig_reversion}


# ── price matrix cache (module-global, refreshed on the web on-demand path) ────
_PX_CACHE: dict = {"ts": 0.0, "data": None}


def _load_prices(db):
    """Per-company sorted (dates, closes) for the visible universe + the NIFTY 50
    benchmark, V-spike-cleaned. Cached 30 min; the series only changes once a day
    after the EOD top-up."""
    import time as _t
    if _PX_CACHE["data"] is not None and (_t.time() - _PX_CACHE["ts"]) < 1800:
        return _PX_CACHE["data"]
    from app import models
    from app.ingest.indianapi_ingester import VISIBLE_UNIVERSE
    from app.price_hygiene import drop_bad_ticks

    cos = {c.id: c for c in db.query(models.Company).all()
           if (c.ticker or "").upper() in VISIBLE_UNIVERSE}
    raw: dict[int, list] = {}
    for hp in (db.query(models.HistoricalPrice.company_id, models.HistoricalPrice.date,
                        models.HistoricalPrice.close)
                 .filter(models.HistoricalPrice.company_id.in_(list(cos.keys())))
                 .order_by(models.HistoricalPrice.company_id, models.HistoricalPrice.date).all()):
        if hp[2]:
            raw.setdefault(hp[0], []).append({"date": hp[1], "close": hp[2]})
    series = {}
    for cid, rows in raw.items():
        clean = drop_bad_ticks(rows)
        if len(clean) >= 60:
            series[cid] = ([r["date"] for r in clean], [r["close"] for r in clean])

    # NIFTY 50 benchmark from Dhan (same path the index chart uses). If Dhan is
    # unconfigured/unavailable, bench stays None and we fall back to an
    # equal-weight-universe benchmark — a fair yardstick for a selection strategy.
    bench = None
    try:
        from app.dhan import client, instruments
        if client.configured():
            sid = instruments.index_security_id("NIFTY 50")
            if sid:
                frm = (_dt.date.today() - _dt.timedelta(days=365 * 5 + 7)).isoformat()
                rows = client.historical_daily(sid, frm, _dt.date.today().isoformat(),
                                               exchange_segment="IDX_I", instrument="INDEX")
                pts = [(r["date"], r["close"]) for r in (rows or [])
                       if r.get("date") and r.get("close")]
                pts.sort()
                if len(pts) >= 60:
                    bench = ([d for d, _ in pts], [c for _, c in pts])
    except Exception:
        bench = None

    meta = {cid: {"ticker": co.ticker, "name": co.name, "sector": co.sector}
            for cid, co in cos.items()}
    data = {"series": series, "bench": bench, "meta": meta}
    _PX_CACHE["data"], _PX_CACHE["ts"] = data, _t.time()
    return data


def _rebalance_dates(all_dates, years, freq):
    """First trading day of each month (M) / quarter (Q) within the window."""
    if not all_dates:
        return []
    cutoff = (_dt.date.fromisoformat(all_dates[-1]) - _dt.timedelta(days=int(years * 365.25))).isoformat()
    win = [d for d in all_dates if d >= cutoff]
    out, seen = [], set()
    for d in win:
        y, m = int(d[:4]), int(d[5:7])
        key = (y, m) if freq == "M" else (y, (m - 1) // 3)
        if freq == "Q" and m not in (1, 4, 7, 10):
            # still allow the first available quarter-month if data starts mid-quarter
            pass
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def _drawdown(curve):
    peak, mdd = curve[0], 0.0
    for v in curve:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1.0)
    return mdd


def run_backtest(db, signal="momentum", top_n=15, rebalance="M", years=5):
    signal = signal if signal in _SIGNALS else "momentum"
    rebalance = rebalance if rebalance in _REBAL else "M"
    top_n = max(3, min(50, int(top_n)))
    years = max(1, min(5, float(years)))

    px = _load_prices(db)
    series, bench, meta = px["series"], px["bench"], px["meta"]
    if not series:
        return {"ok": False, "reason": "No price history available yet."}

    # master trading calendar = benchmark dates if present, else union of names
    if bench:
        cal = bench[0]
    else:
        s = set()
        for d, _ in series.values():
            s.update(d)
        cal = sorted(s)
    rdates = _rebalance_dates(cal, years, rebalance)
    if len(rdates) < 4:
        return {"ok": False, "reason": "Not enough history in the window to backtest."}

    sig_fn = _SIGNALS[signal]
    ppy = _REBAL[rebalance][1]

    equity_s, equity_b = [1.0], [1.0]
    period_s, period_b = [], []
    wins = 0
    curve = [{"date": rdates[0], "strategy": 1.0, "benchmark": 1.0}]
    last_holdings = []

    for k in range(len(rdates) - 1):
        t0, t1 = rdates[k], rdates[k + 1]
        # rank eligible names on data up to t0 only
        scored = []
        for cid, (dts, cls) in series.items():
            j = bisect.bisect_right(dts, t0)           # closes strictly ≤ t0
            if j < _MIN_HISTORY:
                continue
            val = sig_fn(dts[:j], cls[:j])
            if val is not None:
                scored.append((val, cid))
        if not scored:
            continue
        scored.sort(reverse=True)
        picks = [cid for _, cid in scored[:top_n]]

        # forward equal-weight return t0 → t1
        rets = []
        for cid in picks:
            dts, cls = series[cid]
            p0, p1 = _asof(dts, cls, t0), _asof(dts, cls, t1)
            if p0 and p1 and p0 > 0:
                rets.append(p1 / p0 - 1.0)
        if not rets:
            continue
        r_s = mean(rets)
        # benchmark return over the same window
        if bench:
            b0, b1 = _asof(bench[0], bench[1], t0), _asof(bench[0], bench[1], t1)
            r_b = (b1 / b0 - 1.0) if (b0 and b1 and b0 > 0) else 0.0
        else:
            ew = []
            for _cid, (dts, cls) in series.items():
                q0, q1 = _asof(dts, cls, t0), _asof(dts, cls, t1)
                if q0 and q1 and q0 > 0:
                    ew.append(q1 / q0 - 1.0)
            r_b = mean(ew) if ew else 0.0

        equity_s.append(equity_s[-1] * (1 + r_s))
        equity_b.append(equity_b[-1] * (1 + r_b))
        period_s.append(r_s)
        period_b.append(r_b)
        if r_s > r_b:
            wins += 1
        curve.append({"date": t1, "strategy": round(equity_s[-1], 4), "benchmark": round(equity_b[-1], 4)})

        if k == len(rdates) - 2:
            for val, cid in scored[:top_n]:
                last_holdings.append({"ticker": meta[cid]["ticker"], "name": meta[cid]["name"],
                                      "sector": meta[cid]["sector"], "signal": round(val, 4)})

    if len(equity_s) < 3:
        return {"ok": False, "reason": "Not enough overlapping price data to backtest."}

    d0 = _dt.date.fromisoformat(curve[0]["date"])
    d1 = _dt.date.fromisoformat(curve[-1]["date"])
    yrs = max(0.25, (d1 - d0).days / 365.25)

    def _metrics(equity, periods):
        total = equity[-1] - 1.0
        cagr = (equity[-1]) ** (1 / yrs) - 1.0 if equity[-1] > 0 else None
        vol = (pstdev(periods) * (ppy ** 0.5)) if len(periods) > 1 else None
        sharpe = ((cagr - _RF) / vol) if (cagr is not None and vol) else None
        return {"total_return": round(total, 4),
                "cagr": round(cagr, 4) if cagr is not None else None,
                "vol": round(vol, 4) if vol is not None else None,
                "sharpe": round(sharpe, 2) if sharpe is not None else None,
                "max_drawdown": round(_drawdown(equity), 4)}

    ms, mb = _metrics(equity_s, period_s), _metrics(equity_b, period_b)
    label, rule, _ = STRATEGIES[signal]
    return {
        "ok": True,
        "signal": signal, "label": label, "rule": rule,
        "rebalance": _REBAL[rebalance][0], "top_n": top_n,
        "start": curve[0]["date"], "end": curve[-1]["date"], "years": round(yrs, 2),
        "periods": len(period_s),
        "strategy": ms, "benchmark": mb,
        "excess_cagr": (round(ms["cagr"] - mb["cagr"], 4)
                        if ms["cagr"] is not None and mb["cagr"] is not None else None),
        "hit_rate": round(wins / len(period_s), 3) if period_s else None,
        "curve": curve,
        "holdings": last_holdings,
        "benchmark_name": "NIFTY 50" if bench else "Equal-weight universe",
        "note": ("Point-in-time price-strategy simulation on 5-yr split-adjusted history, "
                 "equal-weight, no look-ahead. Past simulated performance is not indicative "
                 "of future results — a research tool, not investment advice."),
    }
