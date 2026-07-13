"""
app/manager_engine.py — Fund Manager v4: evidence triangulation.

The v3 conviction score leaned on one pillar — the model's own MoS/verdict.
v4 treats the DCF as ONE witness among several and cross-examines it:

  · Valuation triangulation — model fair value vs analyst consensus target vs
    the name's own 5-year P/E band. When the model is the odd one out (or is
    structurally unreliable: negative intrinsic, MoS beyond sane bounds, LOW
    confidence), it is marked SUSPECT, set aside, and the action says so.
  · Fundamental health — the forensics module's accounting-quality composite
    (adapted Piotroski F, accruals, cash conversion, coverage, leverage) and
    its red flags feed conviction directly. A juicy MoS cannot outrank a
    red-flagged balance sheet.
  · Flow & results — institutional/promoter deltas, PAT momentum, EPS surprise.
  · Momentum — 12-1 return and 50/200-DMA state from the 5-yr price series.
  · Model self-trust — the terminal's OWN VerdictSnapshot ledger is scored
    (did BUY calls beat the universe median over the following 6 months?) per
    valuation sector; the model's vote is weighted by its realized hit-rate.
  · Macro regime — universe breadth (% above 200/50-DMA), Nifty trend from
    the Dhan index series, sector relative strength, and the live commodity
    tape mapped to sector tail/headwinds. Risk-off halves starter tranches.

Signal weights load from KVStore "fm_calibration_v1" (written by
app/manager_calibration.py — information coefficients measured on our own
5-year, full-universe history) with honest priors as fallback.

Nightly, `snapshot_evidence` precomputes the per-name evidence blobs and the
macro block into KVStore so /api/portfolio/analysis stays instant.

Doctrine unchanged: educational decision support, mechanically derived from
published data. NOT investment advice. The owner decides.
"""
from __future__ import annotations

import datetime as _dt
import logging

log = logging.getLogger("manager_engine")

EVIDENCE_KEY = "fm_evidence_v1"
MACRO_KEY = "fm_macro_v1"
CALIBRATION_KEY = "fm_calibration_v1"

# Priors: used until (and blended with) measured ICs from the calibration job.
# Grouped to sum loosely to 1 across the valuation trio + non-valuation set.
PRIOR_WEIGHTS = {
    "val_model": 0.16,      # the DCF/RI fair value — one witness, not the judge
    "val_consensus": 0.12,  # analyst mean target
    "val_band": 0.12,       # own 5-yr P/E percentile
    "quality": 0.22,        # forensics composite
    "momentum": 0.16,       # 12-1 + DMA state
    "flow": 0.08,           # institutional/promoter deltas
    "results": 0.08,        # PAT YoY + EPS surprise
    "growth": 0.06,
}

# Sectors that benefit/suffer when a commodity moves. Coarse and honest —
# used only for one-line context in the PM note, never for conviction math.
_COMMODITY_SECTOR_MAP = [
    ("crudeoil", +1, ("Oil & Gas", "Energy")),
    ("crudeoil", -1, ("Paints", "Aviation", "Chemicals", "Tyres")),
    ("gold", +1, ("Jewellery", "Gold Financiers")),
    ("copper", +1, ("Metals", "Mining")),
    ("naturalgas", -1, ("City Gas", "Utilities")),
]


# ── small pure helpers ───────────────────────────────────────────────────────

def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _pct_of(value, series) -> float | None:
    """Percentile (0-100) of `value` within `series` (ignoring Nones)."""
    xs = sorted(v for v in series if v is not None)
    if value is None or len(xs) < 8:
        return None
    below = sum(1 for v in xs if v <= value)
    return round(100.0 * below / len(xs), 1)


def trailing_pe_band(dated_closes: list[tuple[str, float]],
                     eps_by_fy: dict[int, float],
                     shares_note: str = "current") -> dict | None:
    """Current trailing P/E vs the name's OWN monthly trailing-P/E history.

    dated_closes: [(iso_date, close)] ascending, up to 5y.
    eps_by_fy:    {fiscal_year: EPS} — PAT / shares outstanding, FY = March-end.
    A fiscal year's EPS is considered *known* from 1 July of that calendar year
    (results season + buffer), so the band is point-in-time honest.

    Returns {pe_now, pe_pct, n_obs} or None when the inputs can't support it.
    Loss-making years yield no P/E observation (a negative P/E isn't a band).
    """
    if not dated_closes or not eps_by_fy:
        return None

    def eps_known_at(d: _dt.date) -> float | None:
        # FY2024 (Apr'23–Mar'24) is known from 2024-07-01 onward.
        fy = d.year if (d.month >= 7) else d.year - 1
        for y in (fy, fy - 1):
            e = eps_by_fy.get(y)
            if e is not None and e > 0:
                return e
        return None

    # Month-end sampling of trailing P/E.
    monthly: list[float] = []
    last_key = None
    for iso, close in dated_closes:
        try:
            d = _dt.date.fromisoformat(iso[:10])
        except ValueError:
            continue
        key = (d.year, d.month)
        if key == last_key or not close:
            continue
        last_key = key
        e = eps_known_at(d)
        if e:
            monthly.append(close / e)
    if len(monthly) < 12:
        return None
    iso, close = dated_closes[-1]
    try:
        e_now = eps_known_at(_dt.date.fromisoformat(iso[:10]))
    except ValueError:
        return None
    if not (e_now and close):
        return None
    pe_now = close / e_now
    return {"pe_now": round(pe_now, 1),
            "pe_pct": _pct_of(pe_now, monthly),
            "n_obs": len(monthly),
            "shares_basis": shares_note}


def triangulate(model_mos, cons_upside, band_pct,
                confidence: str | None, intrinsic, weights: dict,
                sector_trust: float | None) -> dict:
    """Cross-examine the three valuation witnesses.

    Returns {score, used, suspect, suspect_reasons} where score ∈ [-1, +1]
    (positive = evidence says cheap), `used` lists the sources that voted,
    and suspect flags a model fair value that should not be trusted.
    """
    reasons = []
    suspect = False

    # Structural reliability gates on the model itself.
    if intrinsic is None or (isinstance(intrinsic, (int, float)) and intrinsic <= 0):
        suspect, r = True, "no usable model fair value"
        reasons.append(r)
    if model_mos is not None and model_mos <= -0.75:
        suspect = True
        reasons.append(f"model says {abs(model_mos)*100:.0f}% above fair — beyond the "
                       "band where our DCFs are historically reliable")
    if model_mos is not None and model_mos >= 3.0:
        suspect = True
        reasons.append("model fair value >4× price — treated as a data artifact")
    if (confidence or "").upper() == "LOW":
        suspect = True
        reasons.append("model self-reports LOW confidence on this name")

    # Democratic gate: model overruled when both other witnesses disagree.
    def _dir(x, dead):
        if x is None:
            return None
        return 1 if x > dead else (-1 if x < -dead else 0)

    m_dir = _dir(model_mos, 0.15)
    c_dir = _dir(cons_upside, 0.10)
    b_dir = None
    if band_pct is not None:
        b_dir = 1 if band_pct <= 30 else (-1 if band_pct >= 70 else 0)
    if (not suspect and m_dir is not None and abs(model_mos or 0) >= 0.40
            and c_dir is not None and b_dir is not None
            and c_dir != m_dir and b_dir != m_dir):
        suspect = True
        reasons.append("model fair value conflicts with BOTH the analyst consensus and "
                       "the name's own 5-yr valuation band — set aside")

    votes: list[tuple[str, float, float]] = []   # (source, normalized score, weight)
    if model_mos is not None and not suspect:
        w = weights.get("val_model", 0.16) * (sector_trust if sector_trust is not None else 0.6)
        votes.append(("model", _clamp(model_mos, -1.0, 1.0), w))
    if cons_upside is not None:
        votes.append(("consensus", _clamp(cons_upside, -1.0, 1.0),
                      weights.get("val_consensus", 0.12)))
    if band_pct is not None:
        votes.append(("band", (50.0 - band_pct) / 50.0, weights.get("val_band", 0.12)))

    den = sum(w for _, _, w in votes)
    score = round(sum(s * w for _, s, w in votes) / den, 3) if den else None
    return {"score": score, "used": [v[0] for v in votes],
            "suspect": suspect, "suspect_reasons": reasons}


# ── conviction ───────────────────────────────────────────────────────────────

def conviction_add(ev: dict, weights: dict, macro: dict | None) -> tuple[int, list[str]]:
    """Evidence-weighted conviction (5-95) + human reasons for an ADD/TOP-UP."""
    c = 20.0
    reasons: list[str] = []
    tri = ev.get("tri") or {}
    q = (ev.get("quality") or {})
    momo = ev.get("momo") or {}
    flow = ev.get("flow") or {}
    res = ev.get("results") or {}

    if tri.get("score") is not None:
        c += _clamp(tri["score"], 0, 1) * 40 * (weights.get("val_model", .16)
                                                + weights.get("val_consensus", .12)
                                                + weights.get("val_band", .12)) / 0.40
        srcs = tri.get("used") or []
        if tri["score"] > 0.10 and srcs:
            reasons.append("undervalued on " + (" + ".join(srcs))
                           + f" (blended {tri['score']*100:+.0f}%)")
    if tri.get("suspect"):
        reasons.append("model fair value SET ASIDE: " + "; ".join(tri.get("suspect_reasons") or []))

    comp = q.get("composite")
    if comp is not None:
        c += (comp - 50.0) / 50.0 * 20 * weights.get("quality", .22) / 0.22
        if comp >= 70:
            reasons.append(f"accounting quality {comp:.0f}/100 ({q.get('grade') or 'clean'})")
    for f in (q.get("red_flags") or [])[:2]:
        c -= 8
        reasons.append(f"forensic red flag: {f}")

    if momo.get("above_200dma") and momo.get("above_50dma"):
        c += 6 * weights.get("momentum", .16) / 0.16
        reasons.append("in an uptrend (above 50 & 200-DMA)")
    elif momo.get("above_200dma") is False:
        c -= 8 * weights.get("momentum", .16) / 0.16
        reasons.append("below its 200-DMA — trend not confirming; no hurry on entry")
    mp = momo.get("mom_pct")
    if mp is not None and mp >= 67:
        c += 6 * weights.get("momentum", .16) / 0.16
        reasons.append(f"12-1 momentum in the top third of the universe")

    d = flow.get("inst_delta")
    if d is not None and abs(d) >= 0.3:
        c += (5 if d > 0 else -5) * weights.get("flow", .08) / 0.08
        reasons.append(f"institutions {'added' if d > 0 else 'cut'} {abs(d):.1f}pp last quarter")
    pdlt = flow.get("promoter_delta")
    if pdlt is not None and pdlt <= -1.0:
        c -= 6
        reasons.append(f"promoter stake fell {abs(pdlt):.1f}pp — governance check")

    py = res.get("pat_yoy")
    if py is not None and abs(py) >= 0.15:
        c += (4 if py > 0 else -4) * weights.get("results", .08) / 0.08
        reasons.append(f"latest quarter PAT {py*100:+.0f}% YoY")
    sp = res.get("surprise_pct")
    if sp is not None and abs(sp) >= 0.02:
        c += (4 if sp > 0 else -4) * weights.get("results", .08) / 0.08
        reasons.append(f"FY EPS {'beat' if sp > 0 else 'missed'} the Street by {abs(sp)*100:.0f}%")

    g = ev.get("growth")
    if g is not None and g > 0.10:
        c += 3 * weights.get("growth", .06) / 0.06

    if macro:
        sec = ev.get("sector")
        if sec and sec in (macro.get("rs_leaders") or []):
            c += 5
            reasons.append(f"{sec} is a sector-momentum leader right now")
        elif sec and sec in (macro.get("rs_laggards") or []):
            c -= 5
            reasons.append(f"{sec} is a sector-momentum laggard right now")
        if macro.get("regime") == "risk_off":
            c -= 6
    # A name whose ONLY case is a suspect model never earns high conviction.
    if tri.get("suspect") and (tri.get("score") is None):
        c = min(c, 42.0)
    return int(_clamp(round(c), 5, 95)), reasons


def conviction_trim(ev: dict, weight_in_book: float | None,
                    weights: dict, macro: dict | None) -> tuple[int, list[str]]:
    """Evidence-weighted conviction for a REVIEW TRIM/EXIT."""
    c = 25.0
    reasons: list[str] = []
    tri = ev.get("tri") or {}
    q = ev.get("quality") or {}
    momo = ev.get("momo") or {}
    flow = ev.get("flow") or {}
    res = ev.get("results") or {}

    if tri.get("score") is not None and tri["score"] < 0:
        c += _clamp(-tri["score"], 0, 1) * 35
        reasons.append("overvalued on " + (" + ".join(tri.get("used") or []))
                       + f" (blended {tri['score']*100:+.0f}%)")
    if tri.get("suspect"):
        reasons.append("model fair value SET ASIDE: " + "; ".join(tri.get("suspect_reasons") or []))

    comp = q.get("composite")
    if comp is not None and comp < 45:
        c += (45.0 - comp) / 45.0 * 15
        reasons.append(f"accounting quality weak ({comp:.0f}/100)")
    for f in (q.get("red_flags") or [])[:2]:
        c += 7
        reasons.append(f"forensic red flag: {f}")

    if weight_in_book is not None and weight_in_book > 0.30:
        c += 10
        reasons.append(f"position is {weight_in_book*100:.0f}% of the book — single-name concentration")

    if momo.get("above_200dma") is False:
        c += 5
        reasons.append("below its 200-DMA")
    d = flow.get("inst_delta")
    if d is not None and abs(d) >= 0.3:
        c += (-3 if d > 0 else 4)
        reasons.append(f"institutions {'added' if d > 0 else 'cut'} {abs(d):.1f}pp last quarter")
    py = res.get("pat_yoy")
    if py is not None and abs(py) >= 0.15:
        c += (-3 if py > 0 else 4)
        reasons.append(f"latest quarter PAT {py*100:+.0f}% YoY")
    sp = res.get("surprise_pct")
    if sp is not None and sp <= -0.02:
        c += 3
        reasons.append(f"FY EPS missed the Street by {abs(sp)*100:.0f}%")

    # Nothing actually wrong → honest low conviction.
    if len(reasons) <= (1 if tri.get("suspect") else 0):
        c = min(c, 30.0)
    return int(_clamp(round(c), 5, 95)), reasons


# ── bulk evidence builder (DB) ───────────────────────────────────────────────

def _closes_dated_by(db, days: int) -> dict[int, list[tuple[str, float]]]:
    """company_id → [(date, close)] ascending, split-adjusted not required for
    band ratios month-to-month (Dhan series is already adjusted upstream)."""
    from app import models
    cutoff = (_dt.date.today() - _dt.timedelta(days=days)).isoformat()
    out: dict[int, list[tuple[str, float]]] = {}
    q = (db.query(models.HistoricalPrice.company_id, models.HistoricalPrice.date,
                  models.HistoricalPrice.close)
           .filter(models.HistoricalPrice.date >= cutoff)
           .order_by(models.HistoricalPrice.date))
    for cid, date, close in q.all():
        if close:
            out.setdefault(cid, []).append((date, close))
    return out


def _series_for_evidence(db, ids: list[int], chunk: int = 250) -> dict[int, dict]:
    """Per company: the last ~260 closes (momo/DMA) + a month-end-sampled 5-yr
    (date, close) series (P/E band). Loaded in company chunks and downsampled
    immediately so peak memory stays flat regardless of universe size."""
    from app import models
    cutoff = (_dt.date.today() - _dt.timedelta(days=5 * 365 + 30)).isoformat()
    out: dict[int, dict] = {}
    for start in range(0, len(ids), chunk):
        sub = ids[start:start + chunk]
        rows = (db.query(models.HistoricalPrice.company_id, models.HistoricalPrice.date,
                         models.HistoricalPrice.close)
                  .filter(models.HistoricalPrice.company_id.in_(sub),
                          models.HistoricalPrice.date >= cutoff)
                  .order_by(models.HistoricalPrice.date).all())
        dated: dict[int, list[tuple[str, float]]] = {}
        for cid, date, close in rows:
            if close:
                dated.setdefault(cid, []).append((date, close))
        for cid, ser in dated.items():
            monthly, last_m = [], None
            for d, c in ser:
                if d[:7] != last_m:
                    last_m = d[:7]
                    monthly.append((d, c))
            if monthly and monthly[-1][0] != ser[-1][0]:
                monthly.append(ser[-1])           # today's point closes the band
            out[cid] = {"recent": [c for _, c in ser[-260:]], "monthly": monthly}
        del rows, dated
    return out


def model_trust_by_sector(db, min_calls: int = 8) -> dict[str, float]:
    """Score the model's own BUY record per valuation sector from
    VerdictSnapshot: of BUY calls ≥126 trading days old, what share beat the
    universe median forward 6-month return? Mapped to a 0.3-1.0 trust weight.
    Sectors without enough aged calls get no entry (caller uses the default)."""
    from app import models
    today = _dt.date.today()
    cut_new = (today - _dt.timedelta(days=185)).isoformat()   # call must be aged
    cut_old = (today - _dt.timedelta(days=720)).isoformat()
    rows = (db.query(models.VerdictSnapshot)
              .filter(models.VerdictSnapshot.date >= cut_old,
                      models.VerdictSnapshot.date <= cut_new,
                      models.VerdictSnapshot.verdict == "BUY").all())
    if not rows:
        return {}
    # first BUY snapshot per (company, month) to avoid oversampling
    seen: set = set()
    calls: list = []
    for r in sorted(rows, key=lambda r: r.date):
        k = (r.company_id, r.date[:7])
        if k in seen:
            continue
        seen.add(k)
        calls.append(r)
    # forward price ≈ latest close vs call price, annl-agnostic comparison set
    from app import models as _m
    latest = {m.company_id: m.price for m in db.query(_m.MarketSnapshot).all()}
    fwd = []
    for r in calls:
        p_now = latest.get(r.company_id)
        if p_now and r.price:
            fwd.append((r, p_now / r.price - 1.0))
    if len(fwd) < min_calls:
        return {}
    med = sorted(x for _, x in fwd)[len(fwd) // 2]
    by_sec: dict[str, list[int]] = {}
    for r, ret in fwd:
        by_sec.setdefault(r.valuation_sector or "—", []).append(1 if ret > med else 0)
    out = {}
    for sec, wins in by_sec.items():
        if len(wins) >= min_calls:
            hit = sum(wins) / len(wins)
            out[sec] = round(_clamp(0.3 + hit * 0.9, 0.3, 1.0), 2)
    return out


def load_calibration(db) -> dict:
    from app import models
    row = db.query(models.KVStore).filter_by(key=CALIBRATION_KEY).first()
    val = (row.value or {}) if row else {}
    weights = dict(PRIOR_WEIGHTS)
    for k, w in (val.get("weights") or {}).items():
        if k in weights and isinstance(w, (int, float)) and 0 <= w <= 1:
            weights[k] = round(0.5 * weights[k] + 0.5 * w, 4)   # shrink toward priors
    return {"weights": weights, "as_of": val.get("as_of"),
            "ic": val.get("ic"), "n_names": val.get("n_names")}


def build_evidence(db) -> dict:
    """Compute the full per-ticker evidence map. Heavy (~seconds for 1000
    names) — run nightly by the scheduler, served from KVStore at request time."""
    from app import models
    from app.forensics import forensic_report
    from app.results_logic import eps_surprise
    from app.signals import ranked_visible

    t0 = _dt.datetime.utcnow()
    cos = {c.id: c for c in db.query(models.Company).all()}
    vals = {v.company_id: v for v in db.query(models.Valuation).all()}
    snaps = {m.company_id: m.price for m in db.query(models.MarketSnapshot).all()}

    # factor sheet (momentum percentile, vol, DMA states come from closes below)
    ranked = {(r.get("ticker") or "").upper(): r for r in ranked_visible(db)}
    mom_pcts = {}
    mom_vals = [(t, r.get("momentum_ret")) for t, r in ranked.items()]
    xs = sorted([v for _, v in mom_vals if v is not None])
    for t, v in mom_vals:
        if v is not None and len(xs) >= 8:
            mom_pcts[t] = round(100.0 * sum(1 for x in xs if x <= v) / len(xs), 1)

    series = _series_for_evidence(db, list(cos))

    # statements + EPS by FY
    hf: dict[int, dict] = {}
    for r in (db.query(models.HistoricalFinancial)
                .order_by(models.HistoricalFinancial.fiscal_year).all()):
        if r.value is None:
            continue
        hf.setdefault(r.company_id, {}).setdefault(int(r.fiscal_year), {}) \
          .setdefault(r.statement_type, {})[r.line_item] = r.value

    insights = {r.company_id: (r.data or {}) for r in db.query(models.CompanyInsight).all()}
    trust = model_trust_by_sector(db)
    cal = load_calibration(db)
    weights = cal["weights"]

    out: dict[str, dict] = {}
    for cid, co in cos.items():
        tk = (co.ticker or "").upper()
        if not tk:
            continue
        v = vals.get(cid)
        price = snaps.get(cid)
        stm = hf.get(cid) or {}
        ins = insights.get(cid) or {}

        # quality (forensics)
        try:
            fr = forensic_report(stm, {"type": co.type}) if stm else {}
        except Exception:
            fr = {}
        red = [f.get("label") for f in (fr.get("flags") or []) if f.get("level") == "red"]
        quality = {"composite": fr.get("composite"), "grade": fr.get("grade"),
                   "red_flags": red[:4]}

        # P/E band (needs EPS by FY + shares)
        band = None
        ser = series.get(cid) or {}
        shares = co.shares_outstanding or 0
        if shares and stm:
            eps_by_fy = {}
            for fy, stmts in stm.items():
                pat = (stmts.get("PL") or {}).get("pat")
                if pat is not None:
                    eps_by_fy[fy] = pat * 1e7 / shares if shares > 1e6 else pat / shares
            monthly = ser.get("monthly") or []
            if monthly and eps_by_fy:
                band = trailing_pe_band(monthly, eps_by_fy)

        # momo: DMA states from the last year of closes
        closes = ser.get("recent") or []
        momo = {}
        if len(closes) >= 60:
            momo["above_50dma"] = closes[-1] >= sum(closes[-50:]) / 50
        if len(closes) >= 220:
            momo["above_200dma"] = closes[-1] >= sum(closes[-200:]) / 200
        if tk in mom_pcts:
            momo["mom_pct"] = mom_pcts[tk]

        own = (ins.get("ownership") or {})
        res = (ins.get("results") or {})
        try:
            sur = eps_surprise(ins.get("forecasts")) or {}
        except Exception:
            sur = {}
        growth = ins.get("growth")
        if isinstance(growth, dict):
            growth = growth.get("revenue") or growth.get("rev_cagr") or None

        sector_trust = trust.get(getattr(v, "valuation_sector", None) or "")
        tri = triangulate(getattr(v, "mos", None),
                          getattr(v, "analyst_upside", None),
                          (band or {}).get("pe_pct"),
                          getattr(v, "confidence", None),
                          getattr(v, "intrinsic", None),
                          weights, sector_trust)

        out[tk] = {
            "ticker": tk, "name": co.name, "sector": co.sector, "price": price,
            "model": {"mos": getattr(v, "mos", None), "verdict": getattr(v, "verdict", None),
                      "confidence": getattr(v, "confidence", None),
                      "intrinsic": getattr(v, "intrinsic", None)},
            "consensus": {"upside": getattr(v, "analyst_upside", None),
                          "rating": getattr(v, "analyst_rating", None),
                          "target": getattr(v, "analyst_target", None)},
            "band": band, "quality": quality, "momo": momo,
            "flow": {"inst_delta": (own.get("institutional") or {}).get("delta"),
                     "promoter_delta": (own.get("promoter") or {}).get("delta")},
            "results": {"pat_yoy": res.get("pat_yoy"),
                        "surprise_pct": sur.get("surprise_pct")},
            "growth": growth,
            "alpha": (ranked.get(tk) or {}).get("alpha_score"),
            "tri": tri,
        }
    log.info(f"manager evidence: {len(out)} names in "
             f"{(_dt.datetime.utcnow() - t0).total_seconds():.1f}s "
             f"(trust sectors: {len(trust)}, cal as_of: {cal.get('as_of')})")
    return {"as_of": _dt.datetime.utcnow().isoformat(timespec='seconds') + "Z",
            "weights": weights, "calibration_as_of": cal.get("as_of"),
            "model_trust": trust, "names": out}


# ── macro regime ─────────────────────────────────────────────────────────────

def macro_regime(db) -> dict:
    """Regime read from our OWN data: universe breadth, Nifty trend (Dhan index
    series), sector relative strength (median 12-1 momentum by sector) and the
    live commodity tape. No third-party 'sentiment' — everything verifiable."""
    from app import models

    dated = _closes_dated_by(db, days=420)
    above50 = above200 = n50 = n200 = 0
    for cid, rows in dated.items():
        closes = [c for _, c in rows]
        if len(closes) >= 60:
            n50 += 1
            if closes[-1] >= sum(closes[-50:]) / 50:
                above50 += 1
        if len(closes) >= 220:
            n200 += 1
            if closes[-1] >= sum(closes[-200:]) / 200:
                above200 += 1
    breadth50 = round(above50 / n50, 3) if n50 else None
    breadth200 = round(above200 / n200, 3) if n200 else None

    # Nifty trend from the Dhan index series (graceful when unconfigured).
    nifty = {}
    try:
        from app.dhan import client, instruments
        if client.configured():
            sid = instruments.index_security_id("NIFTY 50")
            if sid:
                to = _dt.date.today()
                frm = to - _dt.timedelta(days=400)
                rows = client.historical_daily(sid, frm.isoformat(), to.isoformat(),
                                               exchange_segment="IDX_I", instrument="INDEX")
                idx_closes = [r.get("close") for r in (rows or []) if r.get("close")]
                if len(idx_closes) >= 220:
                    last = idx_closes[-1]
                    nifty = {"last": round(last, 1),
                             "above_50dma": last >= sum(idx_closes[-50:]) / 50,
                             "above_200dma": last >= sum(idx_closes[-200:]) / 200,
                             "ret_3m": round(last / idx_closes[-63] - 1, 4) if len(idx_closes) >= 63 else None}
    except Exception as e:
        log.warning(f"macro: nifty trend unavailable ({type(e).__name__})")

    # Sector RS: median 12-1 momentum by sector.
    from app.factors import momentum as _momentum
    by_sec: dict[str, list[float]] = {}
    sec_of = {c.id: (c.sector or "Other") for c in db.query(models.Company).all()}
    for cid, rows in dated.items():
        closes = [c for _, c in rows]
        m = _momentum(closes)
        if m is not None:
            by_sec.setdefault(sec_of.get(cid, "Other"), []).append(m)
    sec_rs = []
    for sec, ms in by_sec.items():
        if len(ms) >= 3:
            ms.sort()
            sec_rs.append({"sector": sec, "n": len(ms), "median_mom": round(ms[len(ms) // 2], 4)})
    sec_rs.sort(key=lambda x: -x["median_mom"])
    leaders = [s["sector"] for s in sec_rs[:3]]
    laggards = [s["sector"] for s in sec_rs[-3:]] if len(sec_rs) > 5 else []

    # Commodity tape (live snapshot; context only).
    commodities = []
    try:
        from app.market_routes import _commodities
        commodities = [{"name": c.get("name"), "pct": c.get("pct")}
                       for c in (_commodities() or []) if c.get("pct") is not None]
    except Exception:
        pass

    # Regime label — deliberately blunt: trend + breadth, nothing exotic.
    regime = "neutral"
    if nifty.get("above_200dma") and (breadth200 or 0) >= 0.55:
        regime = "risk_on"
    elif nifty.get("above_200dma") is False and (breadth200 or 1) <= 0.45:
        regime = "risk_off"

    return {"as_of": _dt.datetime.utcnow().isoformat(timespec='seconds') + "Z",
            "breadth_50dma": breadth50, "breadth_200dma": breadth200,
            "nifty": nifty, "sector_rs": sec_rs[:12],
            "rs_leaders": leaders, "rs_laggards": laggards,
            "commodities": commodities, "regime": regime}


def macro_note(macro: dict) -> str:
    """One PM-note paragraph, strictly from the numbers."""
    if not macro:
        return ""
    bits = []
    reg = macro.get("regime")
    b200 = macro.get("breadth_200dma")
    nif = macro.get("nifty") or {}
    if reg == "risk_on":
        bits.append("Macro tape is constructive")
    elif reg == "risk_off":
        bits.append("Macro tape is defensive")
    else:
        bits.append("Macro tape is mixed")
    if nif.get("above_200dma") is not None:
        bits.append(f"Nifty {'above' if nif['above_200dma'] else 'below'} its 200-DMA"
                    + (f" ({nif['ret_3m']*100:+.1f}% over 3m)" if nif.get("ret_3m") is not None else ""))
    if b200 is not None:
        bits.append(f"{b200*100:.0f}% of the universe holds above its 200-DMA")
    if macro.get("rs_leaders"):
        bits.append("sector momentum favours " + ", ".join(macro["rs_leaders"]))
    hot = [c for c in (macro.get("commodities") or []) if abs(c.get("pct") or 0) >= 2.0]
    for c in hot[:2]:
        bits.append(f"{c['name']} {c['pct']:+.1f}%")
    s = "; ".join(bits) + "."
    if macro.get("regime") == "risk_off":
        s += " New entries sized at half tranche until breadth repairs."
    return s


# ── persistence ──────────────────────────────────────────────────────────────

def _kv_put(db, key: str, value: dict):
    from app import models
    row = db.query(models.KVStore).filter_by(key=key).first()
    if row:
        row.value = value
    else:
        db.add(models.KVStore(key=key, value=value))
    db.commit()


def _kv_get(db, key: str) -> dict | None:
    from app import models
    row = db.query(models.KVStore).filter_by(key=key).first()
    return row.value if row else None


def snapshot_evidence(db) -> dict:
    """Nightly job: rebuild evidence + macro into KVStore. Returns summary."""
    ev = build_evidence(db)
    _kv_put(db, EVIDENCE_KEY, ev)
    mac = macro_regime(db)
    _kv_put(db, MACRO_KEY, mac)
    return {"names": len(ev.get("names") or {}), "regime": mac.get("regime"),
            "as_of": ev.get("as_of")}


def load_evidence(db) -> dict | None:
    return _kv_get(db, EVIDENCE_KEY)


def load_macro(db) -> dict | None:
    return _kv_get(db, MACRO_KEY)
