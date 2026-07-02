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

# Evidence-weighted default blend (sums to 1.0). One place to tune the strategy.
FACTOR_WEIGHTS = {"quality": 0.25, "momentum": 0.25, "value": 0.20, "low_vol": 0.20, "growth": 0.10}


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


def realized_vol(closes, window: int = 126):
    """Annualized volatility of daily returns over the trailing window."""
    s = closes[-window:] if len(closes) >= window else closes
    rets = [s[i] / s[i - 1] - 1 for i in range(1, len(s)) if s[i - 1]]
    if len(rets) < 20:
        return None
    return pstdev(rets) * (252 ** 0.5)


def score_universe(rows, weights: dict | None = None) -> list[dict]:
    """rows: list of dicts, each with at least
        {ticker, mos, roe, pe, pb, closes, growth}
    (plus any passthrough fields like name/sector/price/verdict). Returns the
    rows enriched with `factors` (per-factor 0-100), `alpha_score`, `rank`,
    `momentum_ret`, `volatility`, sorted by alpha_score descending."""
    w = weights or FACTOR_WEIGHTS
    ey = {r["ticker"]: (1.0 / r["pe"] if r.get("pe") and r["pe"] > 0 else None) for r in rows}
    by = {r["ticker"]: (1.0 / r["pb"] if r.get("pb") and r["pb"] > 0 else None) for r in rows}
    mom = {r["ticker"]: trailing_return(r.get("closes") or []) for r in rows}
    vol = {r["ticker"]: realized_vol(r.get("closes") or []) for r in rows}

    r_mos = _pct_ranks([(r["ticker"], r.get("mos")) for r in rows])
    r_ey = _pct_ranks(ey.items())
    r_by = _pct_ranks(by.items())
    r_roe = _pct_ranks([(r["ticker"], r.get("roe")) for r in rows])
    r_mom = _pct_ranks(mom.items())
    r_vol = _pct_ranks(vol.items(), higher_is_better=False)   # low vol ranks high
    r_grw = _pct_ranks([(r["ticker"], r.get("growth")) for r in rows])

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
