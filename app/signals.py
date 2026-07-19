"""
app/signals.py — DB assembly + daily capture for the factor / signal layer.

Centralizes: the input-row assembly the Alpha Score (factors.score_universe)
needs, the estimate-revision "catalyst" signal computed from ConsensusSnapshot
history, and the daily snapshot writers that start the factor track record
(AlphaSnapshot) and the consensus-revision history (ConsensusSnapshot). Neither
history can be backfilled, so capture begins the day this ships.

Backend-only; nothing here touches the parity-tested valuation engine.
"""
from __future__ import annotations
import threading as _threading
import time as _time
from datetime import date as _date

from . import models
from .factors import score_universe


def catalyst_by(db, min_points: int = 2) -> dict:
    """Per-ticker estimate-revision momentum: change in analyst-implied upside
    (fallback: target %) between the earliest and latest consensus snapshot we
    hold. Positive = upgraded. Empty until ≥2 snapshots exist, so the catalyst
    factor simply doesn't participate yet."""
    rows = (db.query(models.ConsensusSnapshot)
              .order_by(models.ConsensusSnapshot.ticker, models.ConsensusSnapshot.date).all())
    hist: dict[str, list] = {}
    for r in rows:
        hist.setdefault(r.ticker, []).append(r)
    out = {}
    for tk, h in hist.items():
        if len(h) < min_points:
            continue
        latest, prior = h[-1], h[0]
        if latest.upside is not None and prior.upside is not None:
            out[tk] = latest.upside - prior.upside
        elif latest.target and prior.target:
            out[tk] = latest.target / prior.target - 1.0
    return out


def _adjusted_closes_by(db, days: int = 420) -> dict:
    """Split/bonus-adjusted closes per company from the Dhan HistoricalPrice
    series (vendor split-adjusted), windowed to the trailing ~420 calendar days
    — enough for canonical 12-1 momentum and a 252-day vol window without
    loading 5 years for the whole universe."""
    import datetime as _dt
    cutoff = (_dt.date.today() - _dt.timedelta(days=days)).isoformat()
    # Dhan's series is ALREADY split/bonus-adjusted by the vendor — do not
    # apply the corporate-action ledger again (double adjustment faked
    # momentum cliffs on every name with a split; caught by the 500-name audit).
    try:
        rows = (db.query(models.HistoricalPrice)
                  .filter(models.HistoricalPrice.date >= cutoff)
                  .order_by(models.HistoricalPrice.company_id,
                            models.HistoricalPrice.date).all())
    except Exception:
        db.rollback()
        return {}
    from .price_hygiene import drop_bad_ticks
    grouped: dict[int, list] = {}
    for hp in rows:
        grouped.setdefault(hp.company_id, []).append({"close": hp.close})
    return {cid: [r["close"] for r in drop_bad_ticks(series)]
            for cid, series in grouped.items()}


def surprise_by(db, max_age_days: int = 100) -> dict:
    """Per-ticker latest EPS surprise (%) from the stored forecast blobs — the
    same parse the Results scoreboard uses (results_logic.eps_surprise). Only
    surprises reported within `max_age_days` participate: post-earnings drift
    persists ~3 months, so a stale beat shouldn't score forever. Undated
    surprises are excluded rather than guessed at."""
    import datetime as _dt
    from .results_logic import eps_surprise
    cutoff = _dt.date.today() - _dt.timedelta(days=max_age_days)
    cos = {c.id: (c.ticker or "").upper() for c in db.query(models.Company).all()}
    out = {}
    try:
        insights = db.query(models.CompanyInsight).all()
    except Exception:
        db.rollback()
        return out
    for r in insights:
        tk = cos.get(r.company_id)
        if not tk or not r.data:
            continue
        s = eps_surprise((r.data or {}).get("forecasts"))
        if not s or s.get("surprise_pct") is None:
            continue
        try:
            reported = _dt.date.fromisoformat(str(s.get("date"))[:10])
        except (ValueError, TypeError):
            continue
        if reported >= cutoff:
            out[tk] = s["surprise_pct"]
    return out


def assemble_factor_rows(db, tickers) -> list[dict]:
    """Build score_universe input rows for `tickers` from precomputed valuations
    + price series + consensus revision + latest EPS surprise. No API calls.
    Prefers the split-adjusted Dhan HistoricalPrice series (longer window →
    12-1 momentum, 252d vol); the 1-yr PricePoint series remains the fallback
    for names Dhan hasn't covered."""
    tset = {(t or "").upper() for t in tickers}
    cos = {c.id: c for c in db.query(models.Company).all() if (c.ticker or "").upper() in tset}
    vals = {}
    try:
        vals = {v.company_id: v for v in db.query(models.Valuation).all()}
    except Exception:
        db.rollback()
    price_by = {m.company_id: m.price for m in db.query(models.MarketSnapshot).all()}
    closes: dict[int, list] = {}
    for p in (db.query(models.PricePoint)
                .order_by(models.PricePoint.company_id, models.PricePoint.t).all()):
        closes.setdefault(p.company_id, []).append(p.close)
    hist = _adjusted_closes_by(db)
    for cid, series in hist.items():
        # Only prefer Dhan when it actually extends the window (short stubs lose
        # to a full 1-yr PricePoint series).
        if len(series) >= max(200, len(closes.get(cid, []))):
            closes[cid] = series
    cat = catalyst_by(db)
    sur = surprise_by(db)
    rows = []
    for cid, co in cos.items():
        v = vals.get(cid)
        if not v:
            continue
        rows.append({
            "ticker": co.ticker, "name": co.name, "sector": co.sector,
            "valuation_sector": v.valuation_sector,
            "price": price_by.get(cid), "mos": v.mos, "roe": v.roe, "pe": v.pe, "pb": v.pb,
            "verdict": v.verdict, "confidence": v.confidence,
            "closes": closes.get(cid, []),
            "growth": v.analyst_upside,
            "catalyst": cat.get((co.ticker or "").upper()),
            "surprise": sur.get((co.ticker or "").upper()),
        })
    return rows


# PERF-01: ranked_visible is the single most expensive computation on the box
# (~15s cold over ~1000 names) and is called by 7+ sites (/api/factors,
# /api/baskets, portfolio x-ray, watchlist, manager engine…) which each kept
# SEPARATE caches — opening the Ideas area cold ran the same 15s compute twice,
# and N concurrent cold requests each rebuilt it (stampede → the memory-pressure
# profile that swap-killed the t3.micro). One shared TTL cache + a rebuild lock:
# the first caller computes, everyone else waits for (or reuses) that result.
_RANKED_TTL = 300.0
_ranked_cache: dict = {"at": 0.0, "rows": None}
_ranked_lock = _threading.Lock()


def ranked_visible(db) -> list[dict]:
    """Alpha-ranked visible universe with the heavy price series stripped.
    Shared 5-min cache across ALL callers; double-checked lock so only one
    thread ever rebuilds while others wait for its result."""
    now = _time.time()
    if _ranked_cache["rows"] is not None and now - _ranked_cache["at"] < _RANKED_TTL:
        return _ranked_cache["rows"]
    with _ranked_lock:
        # Re-check under the lock — another thread may have just rebuilt.
        now = _time.time()
        if _ranked_cache["rows"] is not None and now - _ranked_cache["at"] < _RANKED_TTL:
            return _ranked_cache["rows"]
        from .ingest.indianapi_ingester import VISIBLE_UNIVERSE
        ranked = score_universe(assemble_factor_rows(db, VISIBLE_UNIVERSE))
        for r in ranked:
            r.pop("closes", None)
        _ranked_cache.update(at=_time.time(), rows=ranked)
        return ranked


def snapshot_alpha(db) -> int:
    """Write today's AlphaSnapshot for the visible universe (idempotent/day)."""
    today = _date.today().isoformat()
    ranked = ranked_visible(db)
    tk_to_cid = {(c.ticker or "").upper(): c.id for c in db.query(models.Company).all()}
    existing = {s.company_id for s in db.query(models.AlphaSnapshot).filter_by(date=today).all()}
    n = 0
    for r in ranked:
        cid = tk_to_cid.get((r["ticker"] or "").upper())
        if not cid or cid in existing:
            continue
        f = r.get("factors") or {}
        db.add(models.AlphaSnapshot(
            company_id=cid, ticker=r["ticker"], date=today, price=r.get("price"),
            alpha_score=r.get("alpha_score"), rank=r.get("rank"),
            value=f.get("value"), quality=f.get("quality"), momentum=f.get("momentum"),
            low_vol=f.get("low_vol"), growth=f.get("growth"), catalyst=f.get("catalyst")))
        n += 1
    db.commit()
    return n


def snapshot_consensus(db) -> int:
    """Write today's ConsensusSnapshot from the precomputed valuations' analyst
    fields (idempotent/day)."""
    today = _date.today().isoformat()
    existing = {s.company_id for s in db.query(models.ConsensusSnapshot).filter_by(date=today).all()}
    cos = {c.id: c for c in db.query(models.Company).all()}
    try:
        vals = db.query(models.Valuation).all()
    except Exception:
        db.rollback()
        return 0
    n = 0
    for v in vals:
        if v.company_id in existing or v.company_id not in cos:
            continue
        if v.analyst_target is None and v.analyst_upside is None:
            continue
        db.add(models.ConsensusSnapshot(
            company_id=v.company_id, ticker=(cos[v.company_id].ticker or "").upper(),
            date=today, target=v.analyst_target, upside=v.analyst_upside,
            rating=v.analyst_rating))
        n += 1
    db.commit()
    return n
