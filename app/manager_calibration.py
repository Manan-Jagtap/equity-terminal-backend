"""
app/manager_calibration.py — measure the engine's signals against history.

The honest version of "training": every signal the Fund Manager uses that CAN
be reconstructed point-in-time is computed at ~monthly snapshots over the past
four years across the whole universe, and scored by its information
coefficient (Spearman rank correlation with the NEXT six months' return).
Weights are then set proportional to realized IC — with shrinkage toward the
priors, because four years of one market is evidence, not gospel.

Point-in-time discipline:
  · price signals (momentum, vol, P/E band) use only closes ≤ t;
  · statement signals (quality, growth, accruals) use only fiscal years that
    were PUBLISHED by t (FY-March results assumed known from 1 July);
  · flow / results-surprise / consensus signals have no reconstructable
    history in our store — they keep their priors, and the artifact says so.

Output → KVStore "fm_calibration_v1":
  {as_of, snapshots, n_names, ic: {signal: {mean, std, n}}, weights: {...},
   unmeasured: [...], note}

Run monthly by the scheduler; heavy (~1-2 min for 1000 names) but offline.
"""
from __future__ import annotations

import datetime as _dt
import logging
from bisect import bisect_right

from app.manager_engine import CALIBRATION_KEY, PRIOR_WEIGHTS, _kv_put

log = logging.getLogger("manager_calibration")

FWD_TDAYS = 126           # forward window: ~6 months of trading days
LOOKBACK_YEARS = 4        # snapshot span
CHUNK = 200               # companies per closes-loading chunk


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    """Spearman rank correlation, pure python, ties by average rank."""
    n = len(xs)
    if n < 8:
        return None

    def ranks(vs):
        order = sorted(range(n), key=lambda i: vs[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and vs[order[j + 1]] == vs[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def _month_ends(start: _dt.date, end: _dt.date) -> list[str]:
    out = []
    d = _dt.date(start.year, start.month, 1)
    while d <= end:
        nxt = _dt.date(d.year + (d.month == 12), (d.month % 12) + 1, 1)
        last = nxt - _dt.timedelta(days=1)
        if start <= last <= end:
            out.append(last.isoformat())
        d = nxt
    return out


def _fy_known_at(iso_date: str) -> int:
    """Latest fiscal year (March-end) whose results are public at `iso_date`."""
    d = _dt.date.fromisoformat(iso_date)
    return d.year if d.month >= 7 else d.year - 1


def run_calibration(db) -> dict:
    """Compute ICs and write the calibration artifact. Returns it."""
    from app import models
    from app.factors import momentum as _momentum, realized_vol
    from app.forensics import forensic_report

    t0 = _dt.datetime.utcnow()
    today = _dt.date.today()
    snap_dates = _month_ends(today - _dt.timedelta(days=365 * LOOKBACK_YEARS),
                             today - _dt.timedelta(days=215))   # leave room for fwd 6m

    cos = db.query(models.Company).all()
    sec_of = {c.id: c.type for c in cos}
    ids = [c.id for c in cos]

    # statement-derived per-(company, fy) values, computed once
    hf: dict[int, dict] = {}
    for r in (db.query(models.HistoricalFinancial)
                .order_by(models.HistoricalFinancial.fiscal_year).all()):
        if r.value is None:
            continue
        hf.setdefault(r.company_id, {}).setdefault(int(r.fiscal_year), {}) \
          .setdefault(r.statement_type, {})[r.line_item] = r.value

    qual_by_fy: dict[int, dict[int, float]] = {}
    grow_by_fy: dict[int, dict[int, float]] = {}
    accr_by_fy: dict[int, dict[int, float]] = {}
    for cid, stm in hf.items():
        fys = sorted(stm)
        for i, fy in enumerate(fys):
            sub = {y: stm[y] for y in fys if y <= fy}
            try:
                fr = forensic_report(sub, {"type": sec_of.get(cid)})
                if fr.get("composite") is not None:
                    qual_by_fy.setdefault(cid, {})[fy] = fr["composite"]
            except Exception:
                pass
            pl_now = (stm[fy].get("PL") or {})
            rev = pl_now.get("revenue")
            if i > 0:
                rev_prev = (stm[fys[i - 1]].get("PL") or {}).get("revenue")
                if rev and rev_prev:
                    grow_by_fy.setdefault(cid, {})[fy] = rev / rev_prev - 1.0
            pat = pl_now.get("pat")
            cfo = (stm[fy].get("CF") or {}).get("operating_cf")
            ta = (stm[fy].get("BS") or {}).get("total_assets")
            if pat is not None and cfo is not None and ta:
                accr_by_fy.setdefault(cid, {})[fy] = (pat - cfo) / ta

    def latest_known(d: dict[int, float] | None, fy: int):
        if not d:
            return None
        for y in (fy, fy - 1):
            if y in d:
                return d[y]
        return None

    # cross-sections accumulate per snapshot date
    cross: dict[str, dict[str, list[tuple[float, float]]]] = \
        {dt: {k: [] for k in ("momentum", "low_vol", "val_band",
                              "quality", "growth", "accruals")} for dt in snap_dates}

    cutoff = (today - _dt.timedelta(days=365 * (LOOKBACK_YEARS + 1) + 60)).isoformat()
    survivorship = {"full_history": 0, "thin_history": 0, "no_series": len(ids)}
    for start in range(0, len(ids), CHUNK):
        chunk_ids = ids[start:start + CHUNK]
        rows = (db.query(models.HistoricalPrice.company_id, models.HistoricalPrice.date,
                         models.HistoricalPrice.close)
                  .filter(models.HistoricalPrice.company_id.in_(chunk_ids),
                          models.HistoricalPrice.date >= cutoff)
                  .order_by(models.HistoricalPrice.date).all())
        dated: dict[int, tuple[list[str], list[float]]] = {}
        for cid, date, close in rows:
            if close:
                ds, cs = dated.setdefault(cid, ([], []))
                ds.append(date)
                cs.append(close)

        for cid, (ds, cs) in dated.items():
            survivorship["no_series"] -= 1
            if len(cs) >= 950:                      # ~4 trading years
                survivorship["full_history"] += 1
            else:
                survivorship["thin_history"] += 1
            if len(cs) < 300:
                continue
            # monthly trailing-P/E series for the band signal
            shares = None
            eps_by_fy = {}
            co_stm = hf.get(cid) or {}
            # shares: derive from stored pe & price is circular; use company row
            # (documented drift risk — acceptable for a rank signal)
            pe_series: list[tuple[str, float]] = []
            for c in cos:
                if c.id == cid:
                    shares = c.shares_outstanding
                    break
            if shares:
                for fy, stmts in co_stm.items():
                    pat = (stmts.get("PL") or {}).get("pat")
                    if pat is not None and pat > 0:
                        eps_by_fy[fy] = pat * 1e7 / shares if shares > 1e6 else pat / shares
                last_m = None
                for i, d in enumerate(ds):
                    key = d[:7]
                    if key == last_m:
                        continue
                    last_m = key
                    fy = _fy_known_at(d)
                    e = eps_by_fy.get(fy) or eps_by_fy.get(fy - 1)
                    if e:
                        pe_series.append((d, cs[i] / e))

            for dt in snap_dates:
                # closes up to dt
                idx = bisect_right(ds, dt)
                if idx < 260:
                    continue
                fwd_idx = idx + FWD_TDAYS
                if fwd_idx >= len(cs):
                    continue
                p_now, p_fwd = cs[idx - 1], cs[fwd_idx]
                if not (p_now and p_fwd):
                    continue
                fwd = p_fwd / p_now - 1.0
                window = cs[:idx]

                m = _momentum(window)
                if m is not None:
                    cross[dt]["momentum"].append((m, fwd))
                v = realized_vol(window)
                if v is not None:
                    cross[dt]["low_vol"].append((-v, fwd))   # low vol = good

                if pe_series:
                    hist = [pe for d2, pe in pe_series if d2 <= dt]
                    if len(hist) >= 12:
                        pe_now = hist[-1]
                        below = sum(1 for x in hist if x <= pe_now)
                        pct = 100.0 * below / len(hist)
                        cross[dt]["val_band"].append(((50.0 - pct) / 50.0, fwd))

                fy = _fy_known_at(dt)
                q = latest_known(qual_by_fy.get(cid), fy)
                if q is not None:
                    cross[dt]["quality"].append((q, fwd))
                g = latest_known(grow_by_fy.get(cid), fy)
                if g is not None:
                    cross[dt]["growth"].append((g, fwd))
                a = latest_known(accr_by_fy.get(cid), fy)
                if a is not None:
                    cross[dt]["accruals"].append((-a, fwd))  # fewer accruals = good

    # per-signal IC series across dates — walk-forward split: the last 10
    # usable snapshots are held out (OOS) and never touch the weights.
    oos_dates = set(snap_dates[-10:]) if len(snap_dates) > 16 else set()

    def _ic_stats(dates):
        stats = {}
        for sig in ("momentum", "low_vol", "val_band", "quality", "growth", "accruals"):
            ics = []
            for dt in dates:
                pairs = cross[dt][sig]
                if len(pairs) >= 30:
                    ic = _spearman([p[0] for p in pairs], [p[1] for p in pairs])
                    if ic is not None:
                        ics.append(ic)
            if ics:
                mean = sum(ics) / len(ics)
                var = sum((x - mean) ** 2 for x in ics) / len(ics)
                stats[sig] = {"mean": round(mean, 4), "std": round(var ** 0.5, 4),
                              "n": len(ics)}
        return stats

    ic_out = _ic_stats([d for d in snap_dates if d not in oos_dates])
    ic_oos = _ic_stats(sorted(oos_dates)) if oos_dates else {}

    # weights: scale measured priors by realized IC (floor at 0 → half prior),
    # leave unmeasured priors untouched; load_calibration shrinks 50/50 again.
    sig_to_weight = {"momentum": "momentum", "val_band": "val_band",
                     "quality": "quality", "growth": "growth"}
    weights = {}
    for sig, wkey in sig_to_weight.items():
        ic = (ic_out.get(sig) or {}).get("mean")
        if ic is None:
            continue
        scale = 0.5 + min(max(ic, 0.0) * 12.0, 1.5)      # IC 0→0.5x, 0.04→~1x, ≥0.125→2x
        weights[wkey] = round(PRIOR_WEIGHTS[wkey] * scale, 4)

    artifact = {
        "as_of": _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "snapshots": len(snap_dates), "fwd_tdays": FWD_TDAYS,
        "oos_snapshots": len(oos_dates),
        "n_names": len(ids),
        "ic": ic_out, "ic_oos": ic_oos, "weights": weights,
        "survivorship": survivorship,
        "unmeasured": ["val_model (scored live via VerdictSnapshot trust)",
                       "val_consensus", "flow", "results — no reconstructable history"],
        "note": ("ICs are Spearman rank correlations of the point-in-time signal vs the "
                 "next 6 months' return, computed monthly across the universe on our own "
                 "stored history. Weights come from the TRAINING window only; the last "
                 "10 snapshots are held out and reported as ic_oos. Weights = priors "
                 "scaled by realized IC, then shrunk 50/50 toward priors at load time. "
                 "Survivorship: the panel is today's members — names that left the "
                 "indices aren't in it; treat ICs as modestly optimistic."),
        "runtime_s": round((_dt.datetime.utcnow() - t0).total_seconds(), 1),
    }
    _kv_put(db, CALIBRATION_KEY, artifact)
    log.info(f"calibration done: {artifact['runtime_s']}s, ic={ic_out}")
    return artifact
