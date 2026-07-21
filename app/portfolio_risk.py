"""
app/portfolio_risk.py — book-level risk analytics: historical VaR, max drawdown,
XIRR. Pure math on dated series (DB-free, unit-tested); the route layer feeds it
split-adjusted closes from HistoricalPrice.

The survival math the strategy doc calls "the part most people skip": VaR says
what a bad day looks like, drawdown says what a bad year looked like, XIRR says
what the book actually earned per year after the timing of every buy.
This is a research aid, not investment advice.
"""
from __future__ import annotations
import datetime as _dt


def portfolio_value_series(holdings, closes_by):
    """Daily portfolio value from per-ticker dated closes.

    holdings:  [{ticker, qty}]
    closes_by: {ticker: {"YYYY-MM-DD": close}}
    Uses dates where EVERY covered holding has a close (strict intersection —
    forward-filling would understate volatility). Holdings with no series are
    reported in `uncovered` and excluded, with their weight share in
    `coverage` so the caller can qualify the numbers.
    Returns (series [(date, value)], covered_tickers, uncovered_tickers).
    """
    covered = [h for h in holdings if closes_by.get(h["ticker"])]
    uncovered = [h["ticker"] for h in holdings if not closes_by.get(h["ticker"])]
    if not covered:
        return [], [], uncovered
    dates = None
    for h in covered:
        ds = set(closes_by[h["ticker"]].keys())
        dates = ds if dates is None else dates & ds
    series = []
    for d in sorted(dates or ()):
        v = sum(h["qty"] * closes_by[h["ticker"]][d] for h in covered)
        series.append((d, v))
    return series, [h["ticker"] for h in covered], uncovered


def daily_returns(series):
    """[(date, value)] → [r1, r2, ...] simple daily returns."""
    vals = [v for _, v in series]
    return [vals[i] / vals[i - 1] - 1.0 for i in range(1, len(vals)) if vals[i - 1]]


def annualised_vol(returns):
    """Annualised volatility of the book's daily returns (√252 scaling), as a
    fraction (0.16 = 16%). None when the sample is too thin to mean anything —
    FIX-21 uses it for the 12–18% portfolio-vol band."""
    if not returns or len(returns) < 60:
        return None
    n = len(returns)
    mu = sum(returns) / n
    var = sum((r - mu) ** 2 for r in returns) / (n - 1)
    return (var ** 0.5) * (252 ** 0.5)


def current_drawdown(series):
    """How far below its trailing peak the book sits RIGHT NOW (a NEGATIVE
    fraction; 0 at a fresh high) — distinct from max_drawdown's worst-ever dip.
    FIX-21's −12% alert reads this. None when the series is too short."""
    if not series or len(series) < 2:
        return None
    peak = max(v for _, v in series)
    last = series[-1][1]
    return (last / peak - 1.0) if peak > 0 else None


def hist_var(returns, confidence: float = 0.95):
    """Historical-simulation VaR: the loss at the (1-confidence) quantile of the
    daily return distribution, as a POSITIVE fraction (0.021 = a 95% bad day
    loses ≥2.1%). None when the sample is too thin to mean anything."""
    if not returns or len(returns) < 60:
        return None
    s = sorted(returns)
    # k-th worst observation for the (1-c) tail: n=100, c=95% → s[4] (5th worst).
    idx = max(0, min(len(s) - 1, int(len(s) * (1.0 - confidence)) - 1))
    return max(0.0, -s[idx])


def max_drawdown(series):
    """Deepest peak-to-trough decline. Returns {mdd, peak_date, trough_date}
    with mdd as a POSITIVE fraction, or None when the series is too short."""
    if not series or len(series) < 2:
        return None
    peak_v, peak_d = series[0][1], series[0][0]
    worst = {"mdd": 0.0, "peak_date": peak_d, "trough_date": peak_d}
    for d, v in series:
        if v > peak_v:
            peak_v, peak_d = v, d
        elif peak_v > 0:
            dd = 1.0 - v / peak_v
            if dd > worst["mdd"]:
                worst = {"mdd": dd, "peak_date": peak_d, "trough_date": d}
    return worst


def xirr(cashflows, tol: float = 1e-7, max_iter: int = 200):
    """Annualized money-weighted return from dated flows
    [(date, amount)] — negative = money in (buys), positive = money out /
    terminal value. Bisection on NPV (robust — no Newton blowups). None when
    ill-posed (needs both signs, ≥2 dates, and a bracketable root)."""
    flows = [(d if isinstance(d, _dt.date) else _dt.date.fromisoformat(str(d)[:10]), a)
             for d, a in cashflows if a]
    if len(flows) < 2:
        return None
    if not (any(a < 0 for _, a in flows) and any(a > 0 for _, a in flows)):
        return None
    t0 = min(d for d, _ in flows)
    span_years = max((max(d for d, _ in flows) - t0).days, 1) / 365.0

    def npv(rate):
        return sum(a / (1.0 + rate) ** ((d - t0).days / 365.0) for d, a in flows)

    lo, hi = -0.9999, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        f_mid = npv(mid)
        if abs(f_mid) < tol:
            break
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    rate = (lo + hi) / 2.0
    # A same-week book annualizes tiny moves into absurd rates — suppress until
    # the book has real history.
    if span_years < 30 / 365.0:
        return None
    return rate


def risk_summary(holdings, closes_by, cashflows, total_value):
    """Assemble the risk block the /xray route returns. Every number degrades to
    None (never a fabricated statistic) when the underlying series is too thin."""
    series, covered, uncovered = portfolio_value_series(holdings, closes_by)
    rets = daily_returns(series)
    var = hist_var(rets)
    dd = max_drawdown(series)
    covered_val = sum(h["qty"] * list(closes_by[h["ticker"]].values())[-1]
                      for h in holdings if h["ticker"] in set(covered))
    return {
        "var_95_1d": var,
        "var_95_1d_amount": (var * total_value) if (var is not None and total_value) else None,
        "max_drawdown": dd["mdd"] if dd else None,
        "drawdown_peak": dd["peak_date"] if dd else None,
        "drawdown_trough": dd["trough_date"] if dd else None,
        "vol_annual": annualised_vol(rets),            # FIX-21: 12–18% band
        "current_drawdown": current_drawdown(series),  # FIX-21: −12% alert
        "xirr": xirr(cashflows),
        "observations": len(series),
        "covered": covered, "uncovered": uncovered,
        "coverage": (covered_val / total_value) if (total_value and covered_val) else None,
    }
