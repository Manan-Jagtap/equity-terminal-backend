"""
app/beta.py — a real, CALCULATED equity beta per name (not a flat sector beta).

Method:
  1. Build an equal-weighted "market" from our own 5-yr OHLCV — the cross-sectional
     mean daily return across the covered universe, compounded into an index.
  2. For each stock, regress its ~weekly returns on the market's over ~3 years →
     a raw OLS beta and the fit R².
  3. SHRINK the raw beta toward the sector prior (from sector_params) in
     proportion to the fit: a well-fit, liquid name keeps most of its measured
     beta; a thin, noisy name (low R²) leans on the sector prior. This is the
     standard cure for the unstable single-stock regression betas that plague
     Indian mid/small-caps — empirical where the data supports it, stable where
     it doesn't.

compute_all(db) runs in the batch (recompute / scheduler) and caches
{ticker: {beta, raw, r2, n}} in KVStore. effective_assumptions() reads it on the
valuation path; a name we couldn't fit falls back to the sector beta.
"""
from __future__ import annotations
import datetime as _dt
import logging

log = logging.getLogger(__name__)

_KV_KEY = "computed_betas_v1"
LOOKBACK_DAYS = 800          # ~3 trading years
STEP = 5                     # sample ~weekly (every 5 trading days)
MIN_POINTS = 24             # need ~half a year of weekly points to trust a fit
BETA_FLOOR, BETA_CEIL = 0.4, 1.8


def regress(xs: list[float], ys: list[float]):
    """OLS slope of ys on xs plus R². None when degenerate."""
    n = len(xs)
    if n < MIN_POINTS:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    varx = sum((x - mx) ** 2 for x in xs)
    vary = sum((y - my) ** 2 for y in ys)
    if varx <= 0 or vary <= 0:
        return None
    beta = cov / varx
    r2 = (cov * cov) / (varx * vary)
    return beta, r2, n


def shrink(raw: float, r2: float, prior: float) -> float:
    """Vasicek-style shrinkage toward the sector prior, weighted by fit. At
    R²≥0.5 we trust the raw beta up to 80%; a poor fit collapses onto the prior."""
    w = max(0.0, min(0.8, r2 / 0.5))
    b = w * raw + (1 - w) * prior
    return round(max(BETA_FLOOR, min(BETA_CEIL, b)), 3)


def _weekly_returns(index_by_date: dict, dates: list[str]):
    """Returns sampled every STEP dates from an index-level dict, keyed by date."""
    sampled = dates[::STEP]
    out = {}
    for i in range(1, len(sampled)):
        p, c = index_by_date.get(sampled[i - 1]), index_by_date.get(sampled[i])
        if p and c:
            out[sampled[i]] = c / p - 1
    return out


def _nifty_index(lookback_days: int):
    """NIFTY 50 daily closes {date: close} from Dhan — the proper cap-weighted
    market. None when Dhan is unconfigured/down (caller falls back)."""
    try:
        from app.dhan import client, instruments
        if not client.configured():
            return None
        sid = instruments.index_security_id("NIFTY 50")
        if not sid:
            return None
        to = _dt.date.today()
        frm = to - _dt.timedelta(days=lookback_days + 10)
        rows = client.historical_daily(sid, frm.isoformat(), to.isoformat(),
                                       exchange_segment="IDX_I", instrument="INDEX")
        idx = {r["date"][:10]: r["close"] for r in (rows or [])
               if r.get("date") and r.get("close")}
        return idx if len(idx) > 100 else None
    except Exception:
        return None


def _universe_index(by_co: dict, all_dates: list) -> dict:
    """Equal-weighted universe index (cross-sectional mean daily return) — the
    fallback market when the Nifty series isn't available."""
    prev, idx, lvl = {}, {}, 100.0
    for d in all_dates:
        rets = []
        for cid, m in by_co.items():
            c = m.get(d)
            if c is None:
                continue
            p = prev.get(cid)
            if p:
                rets.append(c / p - 1)
            prev[cid] = c
        if len(rets) >= 20:
            lvl *= (1 + sum(rets) / len(rets))
            idx[d] = lvl
    return idx


def compute_all(db) -> dict:
    """Compute + cache every name's shrunk beta. Returns {ticker: {...}}."""
    from app import models, sector_params as SP
    cutoff = (_dt.date.today() - _dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
    rows = (db.query(models.HistoricalPrice.company_id, models.HistoricalPrice.date,
                     models.HistoricalPrice.close)
              .filter(models.HistoricalPrice.date >= cutoff)
              .order_by(models.HistoricalPrice.date).all())
    by_co: dict = {}
    for cid, d, c in rows:
        if c:
            by_co.setdefault(cid, {})[d] = c
    if not by_co:
        return {}

    all_dates = sorted({d for m in by_co.values() for d in m})
    # Market: the NIFTY 50 (cap-weighted, the standard CAPM benchmark) when
    # available, else our equal-weighted universe.
    mkt_idx = _nifty_index(LOOKBACK_DAYS)
    market = "NIFTY 50"
    if not mkt_idx:
        mkt_idx = _universe_index(by_co, all_dates)
        market = "equal-weighted universe"
    mkt_dates = sorted(mkt_idx)
    mkt_wk = _weekly_returns(mkt_idx, mkt_dates)
    if len(mkt_wk) < MIN_POINTS:
        return {}

    co_of = {c.id: c for c in db.query(models.Company).all()}
    out: dict = {}
    for cid, m in by_co.items():
        co = co_of.get(cid)
        if not co:
            continue
        stock_wk = _weekly_returns(m, [d for d in mkt_dates if d in m])
        common = [d for d in mkt_wk if d in stock_wk]
        r = regress([mkt_wk[d] for d in common], [stock_wk[d] for d in common])
        # DAT-04: the shrinkage prior must be the CLASSIFIED valuation sector's
        # beta — co.sector is the raw vendor string ("Information Technology"),
        # which SECTOR_PARAMS doesn't key on, so it silently fell back to the
        # MANUFACTURING default (1.0) for every name. Same derivation as
        # assemble.py: ticker override, then keyword classification.
        vsec = (SP.TICKER_OVERRIDES.get((co.ticker or "").upper())
                or SP.classify_valuation_sector(co.sector, getattr(co, "template_code", None)))
        prior = SP.params(vsec).get("beta") or 1.0
        if not r:
            continue
        raw, r2, n = r
        out[co.ticker.upper()] = {"beta": shrink(raw, r2, prior),
                                  "raw": round(raw, 3), "r2": round(r2, 3),
                                  "n": n, "prior": round(prior, 3), "market": market}
    try:
        row = db.query(models.KVStore).filter_by(key=_KV_KEY).first()
        payload = {"as_of": _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
                   "market": market, "betas": out}
        if row:
            row.value = payload
        else:
            db.add(models.KVStore(key=_KV_KEY, value=payload))
        db.commit()
    except Exception as e:
        db.rollback()
        log.warning("beta cache write failed: %s", e)
    log.info("computed betas for %d names", len(out))
    return out


def stored(db) -> dict:
    """{ticker: {beta, raw, r2, n, prior}} from the cache, or {}."""
    try:
        from app import models
        row = db.query(models.KVStore).filter_by(key=_KV_KEY).first()
        return (row.value or {}).get("betas", {}) if row else {}
    except Exception:
        if db:
            db.rollback()
        return {}


def beta_for(db, ticker: str):
    """The one name's calculated-beta record, or None."""
    return stored(db).get((ticker or "").upper())
