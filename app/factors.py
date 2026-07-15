"""
app/factors.py — transparent multi-factor cross-sectional scoring ("Alpha Score").

Ranks the universe on the factors with the strongest evidence in Indian equities
— quality, momentum, low-volatility, value, growth — into a single 0-100 Alpha
Score plus a factor breakdown, so the terminal can turn 100 names into a short
list of ideas that a disciplined, evidence-based process would surface.

Evidence (18-yr NSE factor backtests): quality-momentum and multi-factor blends
delivered the best risk-adjusted returns (~+4% CAGR alpha vs Nifty 50 for a net
multi-factor sleeve; low-vol beat the index across rolling windows with smaller
drawdowns). The default weights therefore tilt toward quality + momentum, with
low-vol tempering drawdowns and value/growth as diversifiers.

This is a research/ranking aid, NOT investment advice, and NOT a promise of
returns. Pure ranking math here; the route layer assembles inputs from the DB
(precomputed valuations + 1-yr price series + insight growth).
"""
from __future__ import annotations
from statistics import pstdev

# Evidence-weighted default blend. The five core factors sum to 1.0; `catalyst`
# (estimate-revision momentum) and `surprise` (latest EPS beat/miss vs consensus
# — post-earnings drift persists ~3 months) are layered on top and only
# participate when their data exists — otherwise they're None and the score
# re-normalizes over the factors that do, i.e. exactly the prior behaviour.
# One place to tune the strategy.
FACTOR_WEIGHTS = {"quality": 0.25, "momentum": 0.25, "value": 0.20, "low_vol": 0.20,
                  "growth": 0.10, "catalyst": 0.12, "surprise": 0.10}


def _pct_ranks(items, higher_is_better: bool = True) -> dict:
    """items: iterable of (key, value|None) → {key: percentile 0..100} computed
    over the non-None values only. Best value → 100."""
    pairs = [(k, v) for k, v in items if v is not None]
    if not pairs:
        return {}
    pairs.sort(key=lambda x: x[1], reverse=not higher_is_better)
    n = len(pairs)
    if n == 1:
        return {pairs[0][0]: 50.0}
    return {k: round(100.0 * rank / (n - 1), 1) for rank, (k, _) in enumerate(pairs)}


def trailing_return(closes, lookback: int = 126, skip: int = 21):
    """12-1 style momentum: total return over `lookback` trading days ending
    `skip` days ago (skipping the most recent ~month avoids short-term reversal)."""
    if not closes or len(closes) < lookback + skip + 1:
        return None
    end, start = closes[-1 - skip], closes[-1 - skip - lookback]
    if not start or start <= 0:
        return None
    return end / start - 1.0


def momentum(closes):
    """Canonical 12-1 momentum (231d lookback, 21d skip) when at least a year of
    history exists — the horizon with the strongest cross-sectional evidence —
    falling back to 6-1 on shorter series (e.g. a freshly onboarded name)."""
    if closes and len(closes) >= 231 + 21 + 1:
        return trailing_return(closes, lookback=231, skip=21)
    return trailing_return(closes)


def realized_vol(closes, window: int = 252):
    """Annualized volatility of daily returns over the trailing window
    (a full trading year when the series allows; shorter series truncate)."""
    s = closes[-window:] if len(closes) >= window else closes
    rets = [s[i] / s[i - 1] - 1 for i in range(1, len(s)) if s[i - 1]]
    if len(rets) < 20:
        return None
    return pstdev(rets) * (252 ** 0.5)


def _sma(xs, n):
    return sum(xs[-n:]) / n if len(xs) >= n else None


def rsi(closes, n: int = 14):
    """Wilder-style RSI over the last n periods. None on too-short a series."""
    if not closes or len(closes) < n + 1:
        return None
    gains = losses = 0.0
    for i in range(-n, 0):
        ch = closes[i] - closes[i - 1]
        gains += ch if ch > 0 else 0
        losses += -ch if ch < 0 else 0
    if losses == 0:
        return 100.0
    rs = (gains / n) / (losses / n)
    return round(100 - 100 / (1 + rs), 1)


def technicals(closes, vols=None) -> dict | None:
    """Transparent technical read from a close series (+ optional volumes):
    DMA states, golden/death cross, RSI, 52-week-range position, 12-1 momentum,
    and volume vs its 50-day average. Every field None-safe on short history."""
    if not closes or len(closes) < 20:
        return None
    last = closes[-1]
    s20, s50, s200 = _sma(closes, 20), _sma(closes, 50), _sma(closes, 200)
    win = closes[-252:]
    hi, lo = (max(win), min(win)) if win else (None, None)
    mom = momentum(closes)
    vol_ratio = None
    if vols and len(vols) >= 50:
        avg50 = sum(vols[-50:]) / 50
        if avg50:
            vol_ratio = round(vols[-1] / avg50, 2)
    cross = None
    if s50 is not None and s200 is not None:
        cross = "golden" if s50 >= s200 else "death"
    return {
        "last": round(last, 2),
        "above_20dma": (last >= s20) if s20 else None,
        "above_50dma": (last >= s50) if s50 else None,
        "above_200dma": (last >= s200) if s200 else None,
        "cross": cross,                                        # 50/200 DMA regime
        "rsi14": rsi(closes),
        "pct_from_52w_high": round((last / hi - 1) * 100, 1) if hi else None,
        "pct_from_52w_low": round((last / lo - 1) * 100, 1) if lo else None,
        "mom_12_1": round(mom * 100, 1) if mom is not None else None,
        "vol_vs_50d": vol_ratio,
    }


def score_universe(rows, weights: dict | None = None) -> list[dict]:
    """rows: list of dicts, each with at least
        {ticker, mos, roe, pe, pb, closes, growth}
    (plus any passthrough fields like name/sector/price/verdict). Returns the
    rows enriched with `factors` (per-factor 0-100), `alpha_score`, `rank`,
    `momentum_ret`, `volatility`, sorted by alpha_score descending."""
    w = weights or FACTOR_WEIGHTS
    ey = {r["ticker"]: (1.0 / r["pe"] if r.get("pe") and r["pe"] > 0 else None) for r in rows}
    by = {r["ticker"]: (1.0 / r["pb"] if r.get("pb") and r["pb"] > 0 else None) for r in rows}
    mom = {r["ticker"]: momentum(r.get("closes") or []) for r in rows}
    vol = {r["ticker"]: realized_vol(r.get("closes") or []) for r in rows}

    r_mos = _pct_ranks([(r["ticker"], r.get("mos")) for r in rows])
    r_ey = _pct_ranks(ey.items())
    r_by = _pct_ranks(by.items())
    r_roe = _pct_ranks([(r["ticker"], r.get("roe")) for r in rows])
    r_mom = _pct_ranks(mom.items())
    r_vol = _pct_ranks(vol.items(), higher_is_better=False)   # low vol ranks high
    r_grw = _pct_ranks([(r["ticker"], r.get("growth")) for r in rows])
    r_cat = _pct_ranks([(r["ticker"], r.get("catalyst")) for r in rows])   # revision momentum
    r_sur = _pct_ranks([(r["ticker"], r.get("surprise")) for r in rows])   # EPS beat/miss %

    def blend(*ranks):
        vals = [x for x in ranks if x is not None]
        return sum(vals) / len(vals) if vals else None

    out = []
    for r in rows:
        tk = r["ticker"]
        factors = {
            "value":    blend(r_mos.get(tk), r_ey.get(tk), r_by.get(tk)),
            "quality":  blend(r_roe.get(tk)),
            "momentum": r_mom.get(tk),
            "low_vol":  r_vol.get(tk),
            "growth":   r_grw.get(tk),
            "catalyst": r_cat.get(tk),
            "surprise": r_sur.get(tk),
        }
        num = den = 0.0
        for k, wt in w.items():
            if factors.get(k) is not None:
                num += wt * factors[k]
                den += wt
        alpha = round(num / den, 1) if den > 0 else None
        out.append({
            **r,
            "factors": {k: (round(v, 1) if v is not None else None) for k, v in factors.items()},
            "momentum_ret": mom.get(tk), "volatility": vol.get(tk),
            "alpha_score": alpha,
        })
    out.sort(key=lambda x: (x["alpha_score"] is not None, x["alpha_score"] or 0.0), reverse=True)
    for i, r in enumerate(out, 1):
        r["rank"] = i
    return out


def portfolio_xray(items, cap: float = 0.25, low_alpha: float = 40.0) -> dict:
    """Portfolio-level factor/risk X-ray + position-sizing suggestions.

    items: [{ticker, sector, value, alpha_score, factors:{...}, volatility, verdict}].
    MUTATES each item to add `weight` (actual = value/total), `suggested_weight`
    (inverse-volatility target, capped at `cap` and renormalized), and `flags`.
    Returns portfolio aggregates: value-weighted Alpha & factor exposures, a
    weighted volatility proxy, sector concentration (+ HHI), and the top weight.
    Pure math — no DB. This is a research/sizing aid, not investment advice."""
    priced = [i for i in items if i.get("value")]
    total = sum(i["value"] for i in priced)

    # Inverse-volatility target weights (risk parity, summing to 1). The `cap` is
    # applied as a concentration FLAG, not as a hard constraint on the weights —
    # a small book can't sum to 1 under a tight cap, so forcing it there is
    # misleading. The flag tells the user where they're over-concentrated.
    inv = {i["ticker"]: (1.0 / i["volatility"]) for i in priced
           if i.get("volatility") and i["volatility"] > 0}
    s = sum(inv.values())
    sug = {t: inv[t] / s for t in inv} if s else {}

    for i in items:
        i["weight"] = (i["value"] / total) if (total and i.get("value")) else None
        i["suggested_weight"] = sug.get(i["ticker"])
        flags = []
        if i.get("weight") is not None and i["weight"] > cap:
            flags.append(f"Over {int(cap*100)}% — concentration risk")
        if i.get("alpha_score") is not None and i["alpha_score"] < low_alpha:
            flags.append("Low Alpha")
        i["flags"] = flags

    def wavg(key, in_factors=False):
        num = den = 0.0
        for i in priced:
            v = (i.get("factors") or {}).get(key) if in_factors else i.get(key)
            if v is not None:
                num += i["value"] * v
                den += i["value"]
        return (num / den) if den else None

    sect = {}
    for i in priced:
        s = i.get("sector") or "—"
        sect[s] = sect.get(s, 0.0) + i["value"]
    sector_conc = (sorted(({"sector": s, "weight": v / total} for s, v in sect.items()),
                          key=lambda x: -x["weight"]) if total else [])

    return {
        "total_value": total, "n": len(priced),
        "weighted_alpha": wavg("alpha_score"),
        "factor_exposure": {k: wavg(k, in_factors=True)
                            for k in ("value", "quality", "momentum", "low_vol", "growth", "catalyst", "surprise")},
        "est_volatility": wavg("volatility"),
        "top_weight": max((i["weight"] for i in priced if i.get("weight")), default=None),
        "sector_concentration": sector_conc,
        "hhi": round(sum(x["weight"] ** 2 for x in sector_conc), 3) if sector_conc else None,
    }


def sector_strength(ranked) -> list[dict]:
    """Aggregate the Alpha ranking by sector → where the factor tailwind is
    strongest right now. Returns [{sector, n, avg_alpha, factors:{...}}] sorted by
    avg_alpha desc. Pure."""
    by: dict[str, list] = {}
    for r in ranked:
        by.setdefault(r.get("sector") or "—", []).append(r)
    out = []
    for sec, rows in by.items():
        alphas = [r["alpha_score"] for r in rows if r.get("alpha_score") is not None]
        def favg(k):
            vals = [(r.get("factors") or {}).get(k) for r in rows]
            vals = [v for v in vals if v is not None]
            return round(sum(vals) / len(vals), 1) if vals else None
        out.append({
            "sector": sec, "n": len(rows),
            "avg_alpha": round(sum(alphas) / len(alphas), 1) if alphas else None,
            "factors": {k: favg(k) for k in ("value", "quality", "momentum", "low_vol", "growth", "catalyst", "surprise")},
        })
    out.sort(key=lambda x: (x["avg_alpha"] is not None, x["avg_alpha"] or 0.0), reverse=True)
    return out


def alpha_backtest(rows, buckets: int = 5) -> dict:
    """Forward-return-by-Alpha-bucket test. rows: [{ticker, alpha0, price0,
    price_now}] where alpha0/price0 are from a name's FIRST snapshot. Buckets
    names into quantiles by alpha0 (Q1 = highest) and reports each bucket's mean
    forward return + the Q1−Q(last) spread. Honest and forward — only meaningful
    once snapshots span real time. Pure math."""
    usable = [r for r in rows
              if r.get("alpha0") is not None and r.get("price0") and r.get("price_now") and r["price0"] > 0]
    for r in usable:
        r["fwd_ret"] = r["price_now"] / r["price0"] - 1.0
    n = len(usable)
    if n < buckets:
        return {"n": n, "buckets": [], "top_minus_bottom": None}
    ranked = sorted(usable, key=lambda r: r["alpha0"], reverse=True)
    size = n // buckets
    out = []
    for b in range(buckets):
        grp = ranked[b * size: (b + 1) * size] if b < buckets - 1 else ranked[b * size:]
        rets = [r["fwd_ret"] for r in grp]
        out.append({"bucket": b + 1, "label": f"Q{b + 1}", "n": len(grp),
                    "avg_alpha": round(sum(r["alpha0"] for r in grp) / len(grp), 1),
                    "avg_return": (sum(rets) / len(rets)) if rets else None})
    top, bot = out[0]["avg_return"], out[-1]["avg_return"]
    return {"n": n, "buckets": out,
            "top_minus_bottom": (top - bot) if (top is not None and bot is not None) else None}
