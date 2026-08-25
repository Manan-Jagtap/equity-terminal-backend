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
ENGINE_SCHEMA = 8   # bump when evidence blobs gain fields → boot rebuild fires
# ↑ 6 (FIX-19): the per-name `model` block now carries the full FIX-13 valuation
#   contract (gate_state / data_tier / method_dispersion / sensitivity_swing /
#   tv_share) so the desk can gate on it.
# ↑ 7 (FIX-20): each name gains a `risk` block (sigma=annualised vol,
#   adv_inr=median daily traded value) for conviction-aware position sizing.
# ↑ 8 (FIX-22): blob gains `trust_coverage`; model_trust is recomputed on the
#   widened, de-tautologised (beat-the-market) basis — force a fresh rebuild.

# FIX-01 / ENG-01: the nightly ledger append silently failed 16–20 Jul 2026,
# when macro_regime NameError'd on an un-imported macro_data and unwound before
# the ledger block. The gap is PUBLISHED, never backfilled — the ledger's whole
# doctrine is "recorded daily, never reconstructed after the fact", so hindsight
# rows would poison the honesty artifact. Extend this list only if it recurs.
LEDGER_GAPS = [
    {"from": "2026-07-16", "to": "2026-07-20",
     "reason": ("Nightly ledger append halted by a macro-refresh regression "
                "(ENG-01); fixed 2026-07-20. Not backfilled — the ledger is "
                "recorded daily and never reconstructed after the fact.")},
]

# Methodology changes that alter WHICH names are eligible for the ledger, disclosed
# rather than retro-applied. The same doctrine as LEDGER_GAPS runs both ways: we
# never backfill favourable rows AND never scrub unfavourable ones, so an entry
# recorded before a gate existed stays exactly as the engine emitted it that day —
# graded honestly, gate and all. New rules bind forward from their effective date.
LEDGER_NOTES = [
    {"effective": "2026-07-27",
     "change": ("CORR-1 + CORR-3a/b/c (valuation LEVEL correction, second pass). "
                "Near-term growth is now EARNED from return-on-capital quality: a "
                "business that does not out-earn its sector's mature ROIC has its "
                "stage-1 revenue growth capped at 10% (approximately India's "
                "long-run NOMINAL GDP growth) instead of the flat 18% every "
                "non-cyclical previously received. Names at or above the quality "
                "threshold are UNCHANGED. Evidence: on the 314-name independent "
                "calibration set, 104 names (33%) sat pinned at exactly the old "
                "18% clamp and were the worst-calibrated cohort in the book — "
                "median intrinsic 1.73x the independent target-band midpoint and "
                "only 23% within band, against 1.23-1.32x and 36-44% for every "
                "name below the cap. Also reclassified names the keyword "
                "classifier mis-bucketed (31 pharma companies were being valued "
                "on IT_SERVICES economics; payments/fintech fee businesses were "
                "priced as lenders; ports/rail/express were priced as factories). "
                "Measured effect: within-band accuracy 33.0% -> 38.5%, zero hard "
                "breaks. Direction is one-way: this only ever LOWERS an unearned "
                "growth rate, so no verdict was inflated by it. Roughly 30% of "
                "covered names see a lower fair value from this date (median "
                "-17.5%) and about 14 change verdict. DISCLOSED, NOT BACKFILLED "
                "- entries recorded before this date are graded exactly as the "
                "engine emitted them and are NOT recomputed.")},
    {"effective": "2026-07-26",
     "change": ("CORR-6 + CORR-8 (valuation LEVEL correction). Two methodology "
                "changes materially lower most non-financial intrinsics, so every "
                "verdict recorded from this date reflects a more conservative "
                "engine than earlier entries. (a) CORR-6: the explicit DCF stage "
                "now charges reinvestment of g/ROIC when the derived historical "
                "rate is lower — previously 79% of the universe was credited "
                "growth without the capital that funds it. (b) CORR-8: the equity "
                "risk premium moves 5.0% -> 6.5%; 5.0% was a mature-market number "
                "and India carries a country-risk premium on top, so the old value "
                "systematically under-discounted every non-financial. Independent "
                "ground truth corroborated the direction: 246 of 314 scored names "
                "sat ABOVE their target band. Measured effect: within-band "
                "accuracy 15.3% -> 31.5%. DISCLOSED, NOT BACKFILLED — pre-"
                "2026-07-26 entries are graded exactly as the engine recorded them "
                "and are NOT recomputed. Knowingly accepted cost: two names "
                "(TANLA, POWERICA) each moved one step further from the "
                "independent verdict, against the usual 0-hard-break bar; owner-"
                "approved trade for +43 names entering their target band.")},
    {"effective": "2026-07-22",
     "change": ("FIX-19: the ledger now enforces the valuation-contract eligibility "
                "gate — a name whose engine methods disagree beyond tolerance, whose "
                "gate is not clean, or whose data is thin can no longer enter the "
                "top-ideas record. Entries dated before 2026-07-22 predate this gate "
                "and are left untouched (not reconstructed); they are graded as the "
                "engine actually recorded them.")},
    {"effective": "2026-07-23",
     "change": ("VAL-02: (a) a PROVEN durable-franchise non-financial (non-cyclical, "
                "≥6y history, operating ROIC ≥1.4× sector, stable margins) now fades "
                "its terminal ROIC only halfway to the sector level instead of "
                "surrendering its whole return advantage — one-sided, capped at "
                "1.5× sector / 40%; (b) cigarettes moved from the CONSUMER staples "
                "multiple set to a dedicated TOBACCO set (exit EV/EBITDA 28x → 11x, "
                "P/E 34x → 20x) reflecting regulated, taxed, volume-declining "
                "economics. Fair values for gated compounders and tobacco names "
                "change from this date; prior ledger entries are untouched.")},
]

# Priors: used until (and blended with) measured ICs from the calibration job.
# Grouped to sum loosely to 1 across the valuation trio + non-valuation set.
PRIOR_WEIGHTS = {
    "val_model": 0.16,      # the DCF/RI fair value — one witness, not the judge
    "val_consensus": 0.12,  # analyst mean target
    "val_band": 0.12,       # own 5-yr P/E+P/B bands + sector-relative multiple
    "quality": 0.22,        # forensics composite
    "momentum": 0.16,       # 12-1 + DMA state + 52w-high proximity
    "flow": 0.08,           # institutional/promoter deltas
    "results": 0.08,        # PAT YoY + EPS surprise
    "growth": 0.06,
    "catalyst": 0.06,       # analyst estimate-revision momentum
}

# News headline classes that cap an ADD and sharpen a TRIM until reviewed.
_NEWS_RED = ("fraud", "scam", "probe", "investigation", "sebi order", "sebi bars",
             "enforcement directorate", " ed raid", "cbi", "default", "downgrade",
             "resign", "auditor", "pledge invoked", "insolvency", "nclt", "raid",
             "penalty", "show cause", "whistleblower", "delisting")

# /company_news is DEAD on the production IndianAPI host. news_routes
# established that on 12 Jul 2026: the host serves no /company_news at all
# (company stories live in the /stock payload's recentNews block) and /news
# ignores stock_name. news_red_flags kept calling it anyway — up to 60 requests
# per evidence rebuild (Mon-Fri 11:15 UTC, plus every on-demand rebuild in the
# web process) that can only 404, each holding a 10s timeout open and each
# ticking the paid vendor meter against a request the vendor never answered.
# The headline screen was therefore always empty, at full price.
# Re-enable ONLY once the production host answers, i.e. when
#   curl -sS -o /dev/null -w '%{http_code}\n' -H "X-API-Key: $INDIANAPI_KEY" \
#        "$INDIANAPI_BASE/company_news?symbol=TCS"
# returns 200 with a list of articles — and wire vendor_meter.record() into the
# call at the same time, because a call that can succeed makes its outcome real
# evidence about the vendor instead of a guaranteed failure.
_COMPANY_NEWS_ENABLED = False

# Rate-sensitive sectors: an easing cycle is a tailwind (cheaper funding,
# demand revival), tightening a headwind. Conservative ±3 tilt.
_RATE_SENSITIVE = ("bank", "nbfc", "financ", "housing", "realty", "real estate",
                   "auto", "consumer durable", "infrastructure", "construction")

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


def _parse_growth(g) -> float | None:
    """Revenue growth as a FRACTION from IndianAPI's /historical_stats body.
    The real shape is {'Compounded Sales Growth': {'3 Years:':'6%', '5 Years:':
    '10%', 'TTM:':'5%'}, ...} — nested percent strings, NOT a 'revenue'/'rev_cagr'
    key. The old code looked for keys that don't exist, so ev['growth'] was
    ALWAYS None and the whole growth signal was dead (scorecard leg, hidden-gems
    gate, conviction bump). Prefer the 3-year sales CAGR, then 5-year, then TTM."""
    if not isinstance(g, dict):
        try:
            return float(g) if g is not None else None
        except (TypeError, ValueError):
            return None
    comp = g.get("Compounded Sales Growth") or g.get("Compounded Profit Growth")
    if not isinstance(comp, dict):
        return None
    for want in ("3 year", "5 year", "ttm", "1 year"):
        for k, val in comp.items():
            if want in str(k).lower():
                try:
                    return float(str(val).replace("%", "").strip()) / 100.0
                except (TypeError, ValueError):
                    continue
    return None


def _pct_of(value, series) -> float | None:
    """Percentile (0-100) of `value` within `series` (ignoring Nones)."""
    xs = sorted(v for v in series if v is not None)
    if value is None or len(xs) < 8:
        return None
    below = sum(1 for v in xs if v <= value)
    return round(100.0 * below / len(xs), 1)


def trailing_multiple_band(dated_closes: list[tuple[str, float]],
                           denom_by_fy: dict[int, float]) -> dict | None:
    """Current trailing multiple (price / per-share denominator) vs the name's
    OWN monthly history of that multiple. Works for P/E (denom = EPS) and P/B
    (denom = book value per share) alike.

    dated_closes: [(iso_date, close)] ascending, up to 5y.
    denom_by_fy:  {fiscal_year: per-share value} — FY = March-end. A fiscal
    year's number is considered *known* from 1 July of that calendar year
    (results season + buffer), so the band is point-in-time honest.

    Returns {now, pct, n_obs} or None. Non-positive denominators (losses,
    negative book) yield no observation — a negative multiple isn't a band.
    """
    if not dated_closes or not denom_by_fy:
        return None

    def known_at(d: _dt.date) -> float | None:
        fy = d.year if (d.month >= 7) else d.year - 1
        for y in (fy, fy - 1):
            e = denom_by_fy.get(y)
            if e is not None and e > 0:
                return e
        return None

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
        e = known_at(d)
        if e:
            monthly.append(close / e)
    if len(monthly) < 12:
        return None
    iso, close = dated_closes[-1]
    try:
        e_now = known_at(_dt.date.fromisoformat(iso[:10]))
    except ValueError:
        return None
    if not (e_now and close):
        return None
    now = close / e_now
    return {"now": round(now, 2), "pct": _pct_of(now, monthly), "n_obs": len(monthly)}


def trailing_pe_band(dated_closes, eps_by_fy, shares_note: str = "current"):
    """Back-compat wrapper: P/E variant of trailing_multiple_band."""
    b = trailing_multiple_band(dated_closes, eps_by_fy)
    if not b:
        return None
    return {"pe_now": b["now"], "pe_pct": b["pct"], "n_obs": b["n_obs"],
            "shares_basis": shares_note}


def news_red_flags(ticker: str, budget_guard=None) -> list[str]:
    """Headline red-flag screen from the vendor news feed. Budget-guarded and
    fail-silent: with the API quota exhausted (or the feed down) it returns []
    — the engine never invents a clean bill of health, it just says nothing."""
    # Short-circuited, not deleted: the endpoint below died 12 Jul 2026 (see
    # _COMPANY_NEWS_ENABLED). [] is exactly the answer its 404 already produced
    # — minus the request, the 10s timeout and the quota tick.
    if not _COMPANY_NEWS_ENABLED:
        return []
    import os
    import requests as _rq
    key = os.getenv("INDIANAPI_KEY", "").strip()
    if not key:
        return []
    if budget_guard and not budget_guard(1):
        return []
    base = os.getenv("INDIANAPI_BASE", "https://dev.indianapi.in").rstrip("/")
    try:
        # tick() only — this call is deliberately NOT vendor_meter.record()ed.
        # news_routes established on 12 Jul 2026 that the production host has
        # no /company_news (company stories live in /stock's recentNews), so
        # this call answers non-200 whether or not the vendor is up. Recording
        # its inevitable failure would report "vendor_failing" during every
        # evidence rebuild that runs in the web process (portfolio cold start,
        # /api/admin/fm-engine/rebuild) — a hundred consecutive failures with no
        # success between them — when nothing upstream is wrong. Wire record()
        # here only once the call is confirmed to answer; better, stop making it.
        from app import vendor_meter; vendor_meter.tick()  # FIX-07
        r = _rq.get(base + "/company_news", headers={"X-API-Key": key},
                    params={"symbol": ticker}, timeout=10)
        if r.status_code != 200:
            return []
        arr = r.json()
        arr = arr if isinstance(arr, list) else (arr.get("news") or arr.get("data") or [])
    except Exception:
        return []
    hits = []
    for it in arr[:20]:
        title = ((it.get("title") or it.get("headline") or "") if isinstance(it, dict) else str(it)).lower()
        for kw in _NEWS_RED:
            if kw in title:
                hits.append(kw.strip())
                break
    # dedupe, keep at most 3 classes
    return sorted(set(hits))[:3]


def triangulate(model_mos, cons_upside, band_pct,
                confidence: str | None, intrinsic, weights: dict,
                sector_trust: float | None,
                method_dispersion: float | None = None) -> dict:
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
        # FIX-19: the engine's own methods disagreeing beyond tolerance HALVES the
        # model witness's weight. A partially-corroborated fair value still votes,
        # but can't carry the blend the way an agreeing one does. (When dispersion
        # tipped a BUY into LOW CONF upstream, `suspect` is already set and the
        # model vote is dropped entirely — this catches the non-BUY names whose
        # methods disagree without a verdict flip.)
        if method_dispersion is not None and method_dispersion > 2.5:
            w *= 0.5
            reasons.append(f"model methods disagree {method_dispersion:.1f}× (> 2.5×) "
                           "— its valuation vote is down-weighted, not trusted whole")
        votes.append(("model", _clamp(model_mos, -1.0, 1.0), w))
    if cons_upside is not None:
        votes.append(("consensus", _clamp(cons_upside, -1.0, 1.0),
                      weights.get("val_consensus", 0.12)))
    if band_pct is not None:
        votes.append(("band", (50.0 - band_pct) / 50.0, weights.get("val_band", 0.12)))

    den = sum(w for _, _, w in votes)
    score = round(sum(s * w for _, s, w in votes) / den, 3) if den else None
    # A single unchecked witness must not be able to MAX the valuation score —
    # for under-covered small/mid-caps (no consensus, thin own-history → no
    # P/E band) the blend used to collapse to whichever lone leg was present, so
    # one aggressive analyst target or one model MoS could top the ADD ledger
    # with zero corroboration. Attenuate a lone-witness score toward neutral.
    n = len(votes)
    if score is not None and n < 2:
        score = round(score * 0.5, 3)
        reasons.append("only one independent valuation witness — score attenuated "
                       "(no cross-check)")
    return {"score": score, "used": [v[0] for v in votes], "n_witnesses": n,
            "suspect": suspect, "suspect_reasons": reasons}


# ── conviction ───────────────────────────────────────────────────────────────

def _valuation_uncertain(model: dict | None) -> bool:
    """FIX-19 (FM-02): is the valuation's OWN contract telling us the fair value
    isn't solid enough to top the public ledger or earn top conviction?

    True when the engine's gate_state is anything but clean (dispersion / almost-
    all-terminal-value / fragile-to-assumptions / abstaining), its own methods
    disagree beyond 2.5×, or the data tier is explicitly thin (low). Absent
    signals (None) are treated as NO signal — a Valuation row computed before the
    FIX-13 contract existed must never silently empty the ledger, so we act only
    on a POSITIVE flag, never on a missing one.

    This is the gate that keeps AUBANK (16× method dispersion) and PATELENG (both
    independent probes condemn it) out of the top-conviction ledger, however
    juicy the lone consensus/band witness looks."""
    m = model or {}
    gate = m.get("gate_state")
    disp = m.get("method_dispersion")
    tier = m.get("data_tier")
    return ((gate is not None and gate != "clean")
            or (disp is not None and disp > 2.5)
            or tier == "low")


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
        # Long-term lens: a high forensic composite with clean flags is a
        # business that's strong INSIDE (cash-backed earnings, low leverage,
        # improving Piotroski), not just optically cheap — a durable hold.
        if comp >= 75 and not (q.get("red_flags")):
            c += 4
            reasons.append(f"fundamentally strong inside and out — accounting quality "
                           f"{comp:.0f}/100, cash-backed earnings; suits a long-term compounding hold")
        elif comp >= 70:
            reasons.append(f"accounting quality {comp:.0f}/100 ({q.get('grade') or 'clean'})")
    for f in (q.get("red_flags") or [])[:2]:
        c -= 8
        reasons.append(f"forensic red flag: {f}")

    wm = weights.get("momentum", .16) / 0.16
    if momo.get("above_200dma") and momo.get("above_50dma"):
        c += 6 * wm
        reasons.append("in an uptrend (above 50 & 200-DMA)")
    elif momo.get("above_200dma") is False:
        c -= 8 * wm
        reasons.append("below its 200-DMA — trend not confirming; no hurry on entry")
    mp = momo.get("mom_pct")
    if mp is not None and mp >= 67:
        c += 6 * wm
        reasons.append("12-1 momentum in the top third of the universe")
    off_hi = momo.get("off_52w_high")
    if off_hi is not None and off_hi >= -0.05:
        c += 4 * wm
        reasons.append("within 5% of its 52-week high — strength, not weakness")
    vs = momo.get("vol_surge")
    if vs is not None and vs >= 1.5 and momo.get("above_50dma"):
        c += 3 * wm
        reasons.append(f"volume running {vs:.1f}× its yearly average — participation confirms")
    rs = momo.get("rs_sector")
    if rs is not None and abs(rs) >= 0.10:
        c += (4 if rs > 0 else -4) * wm
        reasons.append(f"{'leading' if rs > 0 else 'lagging'} its own sector by {abs(rs)*100:.0f}pp (12-1)")

    cat = ev.get("catalyst")
    if cat is not None and abs(cat) >= 0.03:
        c += (4 if cat > 0 else -4) * weights.get("catalyst", .06) / 0.06
        reasons.append(f"analyst estimates {'rising' if cat > 0 else 'falling'} "
                       f"({cat*100:+.0f}pp implied-upside revision)")

    cc = ev.get("concall") or {}
    if cc.get("tone") is not None and abs(cc["tone"]) >= 0.2:
        c += (4 if cc["tone"] > 0 else -5)
        reasons.append(f"latest concall tone {'confident' if cc['tone'] > 0 else 'cautious'}"
                       + (f", guidance {cc['direction']}" if cc.get("direction") in ("up", "down") else ""))
    ins = ev.get("insider") or {}
    if ins.get("n_sells", 0) >= 2 and (ins.get("net_qty") or 0) < 0:
        c -= 6
        reasons.append(f"insiders net sellers recently ({ins['n_sells']} disposals) — governance watch")
    elif ins.get("n_buys", 0) >= 2 and (ins.get("net_qty") or 0) > 0:
        c += 4
        reasons.append(f"insiders net buyers recently ({ins['n_buys']} acquisitions)")

    for nf in (ev.get("news_flags") or [])[:2]:
        c -= 10
        reasons.append(f"news flag: “{nf}” in recent headlines — review before entry")

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
        stance = (macro.get("rates") or {}).get("stance")
        if stance in ("easing", "tightening") and sec \
                and any(k in sec.lower() for k in _RATE_SENSITIVE):
            c += 3 if stance == "easing" else -3
            reasons.append(f"rate-sensitive sector in an {stance} cycle "
                           f"({'tailwind' if stance == 'easing' else 'headwind'})")
    # A name whose ONLY case is a suspect model never earns high conviction.
    if tri.get("suspect") and (tri.get("score") is None):
        c = min(c, 42.0)
    # FIX-19: hard ceiling of 60 when the valuation contract itself is uncertain
    # (non-clean gate / disagreeing methods / thin data). A juicy consensus or
    # 5-yr-band read can no longer carry a broken-model name into top-conviction
    # territory — the model has to actually corroborate first.
    if _valuation_uncertain(ev.get("model")) and c > 60:
        c = 60.0
        reasons.append("valuation uncertain (engine gate / method dispersion / thin "
                       "data) — conviction capped at 60 until the model corroborates")
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
    rs = momo.get("rs_sector")
    if rs is not None and rs <= -0.10:
        c += 3
        reasons.append(f"lagging its own sector by {abs(rs)*100:.0f}pp (12-1)")
    cat = ev.get("catalyst")
    if cat is not None and cat <= -0.03:
        c += 3
        reasons.append(f"analyst estimates falling ({cat*100:+.0f}pp implied-upside revision)")
    cc = ev.get("concall") or {}
    if cc.get("tone") is not None and cc["tone"] <= -0.2:
        c += 4
        reasons.append("latest concall tone cautious"
                       + (", guidance down" if cc.get("direction") == "down" else ""))
    ins = ev.get("insider") or {}
    if ins.get("n_sells", 0) >= 2 and (ins.get("net_qty") or 0) < 0:
        c += 5
        reasons.append(f"insiders net sellers recently ({ins['n_sells']} disposals)")
    for nf in (ev.get("news_flags") or [])[:2]:
        c += 6
        reasons.append(f"news flag: “{nf}” in recent headlines")
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


def what_would_flip(ev: dict, action: str) -> list[str]:
    """The 1-2 pieces of evidence closest to flipping this call — stated up
    front so the user knows what the engine is watching, not just what it
    concluded. Pure restatement of current margins vs the engine's own gates."""
    tri = ev.get("tri") or {}
    q = ev.get("quality") or {}
    momo = ev.get("momo") or {}
    out: list[str] = []
    if action.startswith(("ADD", "TOP-UP")):
        s = tri.get("score")
        if s is not None and s < 0.30:
            out.append(f"valuation blend fading below +15% (now {s*100:+.0f}%)")
        comp = q.get("composite")
        if comp is not None and comp < 68:
            out.append(f"accounting quality slipping below 55 (now {comp:.0f})")
        if momo.get("above_200dma"):
            out.append("a decisive close below the 200-DMA")
        elif momo.get("above_200dma") is False:
            out.append("already below the 200-DMA — reclaiming it strengthens the case")
        if not out:
            out.append("a forensic red flag or adverse headline appearing")
    else:   # REVIEW TRIM / EXIT
        s = tri.get("score")
        if s is not None and s < 0:
            out.append(f"valuation blend recovering above −10% (now {s*100:+.0f}%)")
        if momo.get("above_200dma") is False:
            out.append("price reclaiming its 200-DMA")
        fl = ev.get("flow") or {}
        if (fl.get("inst_delta") or 0) < 0:
            out.append("institutions turning net buyers")
        res = ev.get("results") or {}
        if (res.get("pat_yoy") or 0) < 0:
            out.append("PAT momentum turning positive")
        if not out:
            out.append("the position shrinking back inside sizing limits")
    return out[:2]


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
    """Per company: the last ~260 closes+volumes (momo/DMA/52w/volume-surge) +
    a month-end-sampled 5-yr (date, close) series (multiple bands). Loaded in
    company chunks and downsampled immediately so peak memory stays flat
    regardless of universe size."""
    from app import models
    cutoff = (_dt.date.today() - _dt.timedelta(days=5 * 365 + 30)).isoformat()
    out: dict[int, dict] = {}
    for start in range(0, len(ids), chunk):
        sub = ids[start:start + chunk]
        rows = (db.query(models.HistoricalPrice.company_id, models.HistoricalPrice.date,
                         models.HistoricalPrice.close, models.HistoricalPrice.volume)
                  .filter(models.HistoricalPrice.company_id.in_(sub),
                          models.HistoricalPrice.date >= cutoff)
                  .order_by(models.HistoricalPrice.date).all())
        dated: dict[int, list[tuple[str, float, float | None]]] = {}
        for cid, date, close, vol in rows:
            if close:
                dated.setdefault(cid, []).append((date, close, vol))
        for cid, ser in dated.items():
            monthly, last_m = [], None
            for d, c, _v2 in ser:
                if d[:7] != last_m:
                    last_m = d[:7]
                    monthly.append((d, c))
            if monthly and monthly[-1][0] != ser[-1][0]:
                monthly.append((ser[-1][0], ser[-1][1]))   # today closes the band
            recent = ser[-260:]                            # aligned (date, close, vol)
            closes260 = [c for _, c, _v2 in recent]
            # FIX-20 risk inputs for conviction-aware sizing (and FIX-21 controls).
            # σ: annualised daily-return vol; ADV: median daily traded VALUE (₹),
            # computed here where close and volume are still aligned (recent_vol
            # below is None-filtered and must NOT be zipped with closes).
            sigma = _annualised_vol(closes260)
            day_vals = sorted(c * v for _, c, v in recent if v and c)
            adv_inr = round(day_vals[len(day_vals) // 2]) if day_vals else None
            out[cid] = {"recent": closes260,
                        "recent_vol": [v for _, _c, v in recent if v],
                        "monthly": monthly,
                        "sigma": sigma, "adv_inr": adv_inr}
        del rows, dated
    return out


def _annualised_vol(closes: list[float]) -> float | None:
    """Annualised volatility from daily simple returns (√252 scaling). None when
    there isn't enough history to be meaningful."""
    if not closes or len(closes) < 30:
        return None
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1]]
    if len(rets) < 20:
        return None
    mu = sum(rets) / len(rets)
    var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
    return round((var ** 0.5) * (252 ** 0.5), 4)


def model_trust_by_sector(db, min_calls: int = 6) -> dict[str, float]:
    """Score the model's own BUY record per valuation sector from VerdictSnapshot:
    of aged BUY calls, what share BEAT THE MARKET (NIFTY-50) from the call date to
    today? Mapped to a 0.3–1.0 trust weight; sectors without enough aged calls get
    no entry (caller uses the 0.6 default).

    FIX-22 (FM-06/ENG-07): two fixes to a metric that was inert AND tautological.
      · It compared each call to the calls' OWN median forward return, so ~50% of
        every large sector "won" by construction — trust pinned near 0.75 and
        measuring nothing. Now the yardstick is the NIFTY-50 over the same window:
        did the BUY actually beat the index? (Feed missing → beat-zero fallback,
        still non-tautological.)
      · Horizons widened (aged ≥ 120 days, 3-yr lookback, min 6 calls/sector) so
        the calibration is no longer empty in production (`model_trust_sectors`
        was 0 — every vote silently used the 0.6 fallback)."""
    from app import models
    from app.benchmark import nifty_return
    today = _dt.date.today()
    cut_new = (today - _dt.timedelta(days=120)).isoformat()    # call must be aged ≥120d
    cut_old = (today - _dt.timedelta(days=3 * 365)).isoformat()
    rows = (db.query(models.VerdictSnapshot)
              .filter(models.VerdictSnapshot.date >= cut_old,
                      models.VerdictSnapshot.date <= cut_new,
                      models.VerdictSnapshot.verdict == "BUY").all())
    if not rows:
        return {}
    # first BUY snapshot per (company, month) to avoid oversampling a standing call
    seen: set = set()
    calls: list = []
    for r in sorted(rows, key=lambda r: r.date):
        k = (r.company_id, r.date[:7])
        if k in seen:
            continue
        seen.add(k)
        calls.append(r)
    latest = {m.company_id: m.price for m in db.query(models.MarketSnapshot).all()}
    by_sec: dict[str, list[int]] = {}
    for r in calls:
        p_now = latest.get(r.company_id)
        if not (p_now and r.price):
            continue
        ret = p_now / r.price - 1.0
        mkt = nifty_return(r.date)                 # market over the SAME window
        won = ret > mkt if mkt is not None else ret > 0.0   # beat market, else beat zero
        by_sec.setdefault(r.valuation_sector or "—", []).append(1 if won else 0)
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

    # sector context: median 12-1 momentum (for RS-vs-sector) and the sector's
    # current P/E distribution (for peer-relative valuation)
    sec_moms: dict[str, list[float]] = {}
    sec_pes: dict[str, list[float]] = {}
    sector_of_tk = {}
    for cid, co in cos.items():
        tk = (co.ticker or "").upper()
        sector_of_tk[tk] = co.sector or "Other"
    for t, r in ranked.items():
        m = r.get("momentum_ret")
        if m is not None:
            sec_moms.setdefault(sector_of_tk.get(t, "Other"), []).append(m)
    for cid, v in vals.items():
        pe = getattr(v, "pe", None)
        if pe and pe > 0:
            co = cos.get(cid)
            if co:
                sec_pes.setdefault(co.sector or "Other", []).append(pe)
    sec_mom_med = {s: sorted(ms)[len(ms) // 2] for s, ms in sec_moms.items() if len(ms) >= 3}

    # analyst estimate-revision momentum (already computed for the Alpha desk)
    try:
        from app.signals import catalyst_by
        catalysts = catalyst_by(db)
    except Exception:
        catalysts = {}

    # news screen budget: bounded shortlist, and only when the vendor budget
    # allows (fail-silent while the monthly quota is exhausted)
    try:
        from app import api_budget
        _news_budget = {"left": 60}

        def _guard(n):
            if _news_budget["left"] < n or api_budget.would_exceed(db, n):
                return False
            _news_budget["left"] -= n
            return True
    except Exception:
        def _guard(n):
            return False

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

    # Concall tone (from the transcript ingester) + insider (SEBI PIT) flow.
    try:
        from app.transcript_ingester import tone_by_ticker
        transcripts = tone_by_ticker(db)
    except Exception:
        transcripts = {}
    try:
        from app.nse_sources import insider_by_ticker, pledge_by_ticker
        insiders = insider_by_ticker(db)
        pledges = pledge_by_ticker(db)
    except Exception:
        insiders = {}
        pledges = {}

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
        # Promoter pledge — the single most predictive Indian-market red flag.
        pl = pledges.get(tk) or {}
        pledged = pl.get("pct_pledged")
        if pledged is not None and pledged >= 20:
            red = [f"Promoter pledge {pledged:.0f}%"] + red
        # carry the balance-sheet metrics too, so the scorecard's SAFETY score can
        # stand on leverage/coverage independently of the profitability composite
        # (they were ~44% collinear when safety just started from the composite).
        _fm = fr.get("metrics") or {}
        quality = {"composite": fr.get("composite"), "grade": fr.get("grade"),
                   "red_flags": red[:4], "pledge_pct": pledged,
                   "net_debt_ebitda": (_fm.get("net_debt_ebitda") or {}).get("value"),
                   "interest_cover": (_fm.get("interest_coverage") or {}).get("value")}

        # Valuation bands: own-history P/E and P/B + the sector-relative P/E.
        band = None
        ser = series.get(cid) or {}
        shares = co.shares_outstanding or 0
        monthly = ser.get("monthly") or []
        pe_b = pb_b = None
        if shares and stm and monthly:
            per_share = lambda x: (x * 1e7 / shares if shares > 1e6 else x / shares)
            eps_by_fy, bvps_by_fy = {}, {}
            for fy, stmts in stm.items():
                pat = (stmts.get("PL") or {}).get("pat")
                if pat is not None:
                    eps_by_fy[fy] = per_share(pat)
                eq = (stmts.get("BS") or {}).get("net_worth") or (stmts.get("BS") or {}).get("equity")
                if eq is not None:
                    bvps_by_fy[fy] = per_share(eq)
            pe_b = trailing_multiple_band(monthly, eps_by_fy) if eps_by_fy else None
            pb_b = trailing_multiple_band(monthly, bvps_by_fy) if bvps_by_fy else None
        sec_pe_pct = None
        pe_now = getattr(v, "pe", None)
        peers = sec_pes.get(co.sector or "Other") or []
        if pe_now and pe_now > 0 and len(peers) >= 6:
            sec_pe_pct = _pct_of(pe_now, peers)
        pcts = [p for p in ((pe_b or {}).get("pct"), (pb_b or {}).get("pct"), sec_pe_pct)
                if p is not None]
        if pcts:
            band = {"pe_now": (pe_b or {}).get("now"), "pe_pct": (pe_b or {}).get("pct"),
                    "pb_pct": (pb_b or {}).get("pct"), "sector_pe_pct": sec_pe_pct,
                    "combined_pct": round(sum(pcts) / len(pcts), 1),
                    "n_obs": (pe_b or pb_b or {}).get("n_obs")}

        # momo: DMA states, 52w-high proximity, volume surge, RS vs sector
        closes = ser.get("recent") or []
        vols = ser.get("recent_vol") or []
        momo = {}
        if len(closes) >= 60:
            momo["above_50dma"] = closes[-1] >= sum(closes[-50:]) / 50
        if len(closes) >= 220:
            momo["above_200dma"] = closes[-1] >= sum(closes[-200:]) / 200
        if len(closes) >= 200:
            momo["off_52w_high"] = round(closes[-1] / max(closes) - 1, 4)
        if len(vols) >= 120:
            v20 = sum(vols[-20:]) / 20
            vbase = sum(vols) / len(vols)
            if vbase > 0:
                momo["vol_surge"] = round(v20 / vbase, 2)
        if tk in mom_pcts:
            momo["mom_pct"] = mom_pcts[tk]
        r_mom = (ranked.get(tk) or {}).get("momentum_ret")
        sec_med = sec_mom_med.get(co.sector or "Other")
        if r_mom is not None and sec_med is not None:
            momo["rs_sector"] = round(r_mom - sec_med, 4)

        own = (ins.get("ownership") or {})
        res = (ins.get("results") or {})
        try:
            sur = eps_surprise(ins.get("forecasts")) or {}
        except Exception:
            sur = {}
        growth = _parse_growth(ins.get("growth"))

        sector_trust = trust.get(getattr(v, "valuation_sector", None) or "")
        tri = triangulate(getattr(v, "mos", None),
                          getattr(v, "analyst_upside", None),
                          (band or {}).get("combined_pct"),
                          getattr(v, "confidence", None),
                          getattr(v, "intrinsic", None),
                          weights, sector_trust,
                          method_dispersion=getattr(v, "method_dispersion", None))

        # headline screen — only for names good enough to surface as actions
        # (bounded vendor calls; silently empty while the quota is exhausted)
        news = []
        if (tri.get("score") is not None and tri["score"] >= 0.15
                and (quality.get("composite") or 0) >= 55 and not red):
            news = news_red_flags(tk, budget_guard=_guard)

        out[tk] = {
            "ticker": tk, "name": co.name, "sector": co.sector, "price": price,
            "model": {"mos": getattr(v, "mos", None), "verdict": getattr(v, "verdict", None),
                      "confidence": getattr(v, "confidence", None),
                      "intrinsic": getattr(v, "intrinsic", None),
                      # FIX-19: the full FIX-13 valuation contract, consumed by the
                      # ledger-eligibility + conviction-cap gates.
                      "gate_state": getattr(v, "gate_state", None),
                      "data_tier": getattr(v, "data_tier", None),
                      "method_dispersion": getattr(v, "method_dispersion", None),
                      "sensitivity_swing": getattr(v, "sensitivity_swing", None),
                      "tv_share": getattr(v, "tv_share", None)},
            "consensus": {"upside": getattr(v, "analyst_upside", None),
                          "rating": getattr(v, "analyst_rating", None),
                          "target": getattr(v, "analyst_target", None),
                          "low": getattr(v, "analyst_low", None),
                          "high": getattr(v, "analyst_high", None)},
            "band": band, "quality": quality, "momo": momo,
            # FIX-20: per-name risk inputs for conviction-aware position sizing.
            "risk": {"sigma": ser.get("sigma"), "adv_inr": ser.get("adv_inr")},
            "flow": {"inst_delta": (own.get("institutional") or {}).get("delta"),
                     "promoter_delta": (own.get("promoter") or {}).get("delta")},
            # eps_surprise returns surprise_pct in PERCENT (−0.4 = −0.4%); the
            # scorecard and conviction consume it as a FRACTION. Convert here so
            # the surprise leg stops saturating and "beat by 500%" text is fixed.
            "results": {"pat_yoy": res.get("pat_yoy"),
                        "surprise_pct": (sur.get("surprise_pct") / 100.0
                                         if sur.get("surprise_pct") is not None else None)},
            "growth": growth,
            "catalyst": catalysts.get(tk),
            "concall": transcripts.get(tk),
            "insider": insiders.get(tk),
            "news_flags": news,
            "alpha": (ranked.get(tk) or {}).get("alpha_score"),
            "tri": tri,
        }
    log.info(f"manager evidence: {len(out)} names in "
             f"{(_dt.datetime.utcnow() - t0).total_seconds():.1f}s "
             f"(trust sectors: {len(trust)}, cal as_of: {cal.get('as_of')})")
    return {"as_of": _dt.datetime.utcnow().isoformat(timespec='seconds') + "Z",
            "schema": ENGINE_SCHEMA,
            "weights": weights, "calibration_as_of": cal.get("as_of"),
            "model_trust": trust,
            # FIX-22: surface trust coverage so engine-status shows whether the
            # "weighted by realised hit-rate" feature is actually operating.
            "trust_coverage": {"sectors": len(trust),
                               "sectors_scored": sorted(trust.keys())},
            "names": out}


# ── macro regime ─────────────────────────────────────────────────────────────

def macro_regime(db) -> dict:
    """Regime read from our OWN data: universe breadth, Nifty trend (Dhan index
    series), sector relative strength (median 12-1 momentum by sector) and the
    live commodity tape. No third-party 'sentiment' — everything verifiable."""
    from app import models
    from app import macro_data   # ENG-01/FIX-01: referenced below (flows,
                                 # activity, forecast) but never imported — the
                                 # unguarded `macro_data.macro_forecast` in the
                                 # return NameError'd every run, freezing the
                                 # macro blob and the public ledger since 15 Jul.

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

    # Nifty trend + India VIX from the Dhan index series (graceful when
    # unconfigured or when the VIX series isn't carried).
    nifty = {}
    vix = None
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
            for vix_name in ("INDIA VIX", "India VIX", "INDIAVIX"):
                vsid = instruments.index_security_id(vix_name)
                if vsid:
                    to = _dt.date.today()
                    frm = to - _dt.timedelta(days=380)
                    vrows = client.historical_daily(vsid, frm.isoformat(), to.isoformat(),
                                                    exchange_segment="IDX_I", instrument="INDEX")
                    vc = [r.get("close") for r in (vrows or []) if r.get("close")]
                    if len(vc) >= 60:
                        vix = {"last": round(vc[-1], 2),
                               "pctile_1y": _pct_of(vc[-1], vc)}
                    break
    except Exception as e:
        log.warning(f"macro: nifty/vix unavailable ({type(e).__name__})")

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

    # FII/DII flows (from the NSE feed in the macro store) — a sustained FII
    # cash-market sell is a market-wide headwind our price data can't see.
    flows = None
    try:
        fii = macro_data.series(db, "fii_net_cr")
        dii = macro_data.series(db, "dii_net_cr")
        if fii:
            fii5 = sum(v for _, v in fii[-5:])
            flows = {"fii_net_5d_cr": round(fii5),
                     "dii_net_5d_cr": round(sum(v for _, v in dii[-5:])) if dii else None,
                     "fii_last": fii[-1][1], "as_of": fii[-1][0]}
    except Exception:
        pass

    # Commodity tape (live snapshot; context only).
    commodities = []
    try:
        from app.market_routes import _commodities
        commodities = [{"name": c.get("name"), "pct": c.get("pct")}
                       for c in (_commodities() or []) if c.get("pct") is not None]
    except Exception:
        pass

    # Rates / inflation / currency from the macro store (RBI DBIE seed +
    # API overlays). Every number carries its as-of date — staleness visible.
    rates = {}
    try:
        from app.macro_data import macro_summary
        rates = macro_summary(db) or {}
    except Exception as e:
        log.warning(f"macro: rates summary unavailable ({type(e).__name__})")

    # High-frequency activity read (GST/e-way/power/auto/UPI/PMI + the cited
    # business-activity composite) — the real-economy pulse our price data misses.
    activity = {}
    try:
        activity = macro_data.activity_read(db) or {}
    except Exception as e:
        log.warning(f"macro: activity read unavailable ({type(e).__name__})")

    # Regime label — deliberately blunt: trend + breadth, nothing exotic.
    # Elevated VIX (top decile of its own year) shades a would-be risk_on down;
    # so does genuine monetary tightening with hot CPI.
    regime = "neutral"
    nifty_trend = nifty.get("above_200dma")           # True / False / None
    if nifty_trend is True and (breadth200 or 0) >= 0.55:
        regime = "risk_on"
    elif nifty_trend is False and (breadth200 or 1) <= 0.45:
        regime = "risk_off"
    elif nifty_trend is None and breadth200 is not None:
        # Nifty trend unavailable (e.g. Dhan unconfigured/down). Rather than pin
        # to neutral while our own breadth is screaming, let breadth alone drive —
        # but only at the EXTREMES, since we've lost the index-trend confirmation.
        if breadth200 >= 0.60:
            regime = "risk_on"
        elif breadth200 <= 0.35:
            regime = "risk_off"
    if regime == "risk_on" and vix and (vix.get("pctile_1y") or 0) >= 90:
        regime = "neutral"
    if (regime == "risk_on" and rates.get("stance") == "tightening"
            and ((rates.get("cpi_yoy") or {}).get("pct") or 0) >= 6.0):
        regime = "neutral"
    # Broad real-economy cooling shades a would-be risk_on down — the market can
    # run ahead of activity, but a cooling pulse is a caution flag, not a driver.
    if regime == "risk_on" and activity.get("label") == "cooling":
        regime = "neutral"

    # Breadth history → regime-change detection (KV, self-pruning to ~2 years).
    today_iso = _dt.date.today().isoformat()
    trend = None
    try:
        hist = _kv_get(db, "fm_breadth_history_v1") or {"rows": []}
        rows_h = [r for r in hist.get("rows", []) if r.get("date") != today_iso]
        if breadth200 is not None:
            rows_h.append({"date": today_iso, "b200": breadth200,
                           "b50": breadth50, "regime": regime})
        rows_h = rows_h[-500:]
        _kv_put(db, "fm_breadth_history_v1", {"rows": rows_h})
        past = [r for r in rows_h[:-1]][-20:]
        if past and breadth200 is not None and past[0].get("b200") is not None:
            delta = breadth200 - past[0]["b200"]
            trend = "improving" if delta >= 0.03 else ("deteriorating" if delta <= -0.03 else "flat")
    except Exception:
        db.rollback()

    return {"as_of": _dt.datetime.utcnow().isoformat(timespec='seconds') + "Z",
            "breadth_50dma": breadth50, "breadth_200dma": breadth200,
            "breadth_trend": trend, "vix": vix, "rates": rates, "flows": flows,
            "nifty": nifty, "sector_rs": sec_rs[:12],
            "rs_leaders": leaders, "rs_laggards": laggards,
            "commodities": commodities, "activity": activity,
            "forecast": macro_data.macro_forecast(db), "regime": regime}


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
        tr = macro.get("breadth_trend")
        bits.append(f"{b200*100:.0f}% of the universe holds above its 200-DMA"
                    + (f" ({tr})" if tr and tr != "flat" else ""))
    vx = macro.get("vix") or {}
    if vx.get("pctile_1y") is not None and vx["pctile_1y"] >= 80:
        bits.append(f"India VIX {vx['last']} — {vx['pctile_1y']:.0f}th percentile of its year, elevated")
    act = macro.get("activity") or {}
    comp = act.get("composite") or {}
    if comp.get("yoy_pct") is not None:
        bits.append(f"Real-economy activity {act.get('label') or 'mixed'} "
                    f"({comp['name']} {comp['yoy_pct']:+.1f}% YoY"
                    + (f", {comp['trend']}" if comp.get('trend') else "") + ")")
    elif act.get("label"):
        bits.append(f"Real-economy activity {act['label']}")
    fl = macro.get("flows") or {}
    if fl.get("fii_net_5d_cr") is not None:
        f5 = fl["fii_net_5d_cr"]
        bits.append(f"FIIs {'bought' if f5 >= 0 else 'sold'} ₹{abs(f5):,.0f} cr net over 5 sessions")
    rt = macro.get("rates") or {}
    g10 = rt.get("gsec_10y") or {}
    if g10.get("last") is not None:
        line = f"10Y G-sec {g10['last']}%"
        if g10.get("chg_3m_bps") is not None:
            line += f" ({g10['chg_3m_bps']:+d}bps over 3m)"
        if rt.get("stance"):
            line += f" — policy {rt['stance'].replace('_', ' ')}"
        bits.append(line)
    cpi = rt.get("cpi_yoy") or {}
    if cpi.get("pct") is not None:
        bits.append(f"CPI {cpi['pct']:+.1f}% YoY")
    ur = rt.get("usdinr") or {}
    if ur.get("last") is not None and ur.get("chg_3m_pct") is not None and abs(ur["chg_3m_pct"]) >= 1.0:
        bits.append(f"USDINR {ur['last']} ({ur['chg_3m_pct']:+.1f}% over 3m — "
                    f"{'rupee weakening' if ur['chg_3m_pct'] > 0 else 'rupee firming'})")
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
    # ARCH-04: delegate to the envelope writer so the FM blobs (the ENG-01
    # staleness surface) carry a kv_meta updated_at stamp. Value shape unchanged.
    from app.kv import kv_put
    kv_put(db, key, value, schema_version=1)


def _kv_get(db, key: str) -> dict | None:
    from app import models
    row = db.query(models.KVStore).filter_by(key=key).first()
    return row.value if row else None


def snapshot_evidence(db) -> dict:
    """Nightly job: rebuild evidence + macro into KVStore, and append today's
    top universe-wide ideas to the EngineCall ledger (the engine's own public
    track record). Returns a summary."""
    ev = build_evidence(db)
    _kv_put(db, EVIDENCE_KEY, ev)

    # Macro is a BEST-EFFORT leg. Its failure must NEVER abort the ledger append
    # (an un-imported macro_data froze the public track record for 5 days exactly
    # this way — ENG-01). On failure: keep the last-good macro for the conviction
    # math, flag it stale, and record the error so /api/health errors_1h and the
    # uptime alert see it. Partial completion of this job is now impossible.
    macro_stale = False
    try:
        mac = macro_regime(db)
        _kv_put(db, MACRO_KEY, mac)
    except Exception as e:
        db.rollback()
        macro_stale = True
        mac = load_macro(db) or {"regime": "neutral", "stale": True}
        log.error(f"macro_regime failed; ledger continues on last-good macro: "
                  f"{type(e).__name__}: {e}")
        try:
            from app.error_log import record_error
            record_error(db, "job:fm_macro_regime", e)
        except Exception:
            pass

    # Ledger: same gates the desk uses, empty-book view, top 15 by conviction.
    n_ledger = 0
    try:
        from app import models
        from app.database import engine as _engine, Base as _Base
        _Base.metadata.create_all(bind=_engine)
        weights = ev.get("weights") or PRIOR_WEIGHTS
        today = _dt.date.today().isoformat()
        scored = []
        for tk, e in (ev.get("names") or {}).items():
            tri, q, momo = e.get("tri") or {}, e.get("quality") or {}, e.get("momo") or {}
            if tri.get("score") is None or tri["score"] < 0.15:
                continue
            if (q.get("composite") or 0) < 55 or (q.get("red_flags") or []):
                continue
            if momo.get("above_200dma") is False and (momo.get("mom_pct") or 50) < 50:
                continue
            # FIX-19: HARD ledger eligibility — a name whose own valuation contract
            # is uncertain (non-clean gate / methods disagree > 2.5× / thin data)
            # can never enter the public track record, however good its other legs
            # look. This is what excludes AUBANK/PATELENG-class names from top-15.
            if _valuation_uncertain(e.get("model")):
                continue
            cv, _ = conviction_add(e, weights, mac)
            scored.append((cv, tk, e))
        scored.sort(key=lambda x: -x[0])
        for cv, tk, e in scored[:15]:
            row = (db.query(models.EngineCall)
                     .filter_by(date=today, ticker=tk).first())
            if not row:
                db.add(models.EngineCall(
                    date=today, ticker=tk, action="ADD CANDIDATE",
                    conviction=cv, price=e.get("price"),
                    val_blend=(e.get("tri") or {}).get("score"),
                    quality=(e.get("quality") or {}).get("composite"),
                    suspect=1 if (e.get("tri") or {}).get("suspect") else 0))
                n_ledger += 1
        db.commit()
    except Exception as e:
        db.rollback()
        log.error(f"engine ledger write failed: {type(e).__name__}: {e}")
        try:
            from app.error_log import record_error
            record_error(db, "job:fm_ledger_write", e)
        except Exception:
            pass

    return {"names": len(ev.get("names") or {}), "regime": mac.get("regime"),
            "as_of": ev.get("as_of"), "ledger_added": n_ledger,
            "trust_sectors": (ev.get("trust_coverage") or {}).get("sectors", 0),
            "macro_stale": macro_stale}


def load_evidence(db) -> dict | None:
    return _kv_get(db, EVIDENCE_KEY)


def load_macro(db) -> dict | None:
    return _kv_get(db, MACRO_KEY)
