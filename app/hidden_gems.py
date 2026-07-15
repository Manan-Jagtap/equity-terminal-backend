"""
app/hidden_gems.py — the Fund Manager's hidden-gems / multibagger finder.

Surfaces UNDER-FOLLOWED quality compounders from the full universe: small- and
mid-cap names with clean forensics and governance, durable returns on capital,
a real growth engine, and a price that isn't already discounting the story —
each with an explicit, HONEST multibagger thesis (the ingredients, never a
promise) and the risks spelled out.

It is a synthesis, not a new model: every input is the Fund Manager v4 evidence
blob (valuation triangulation, forensic quality, momentum, growth, flows, pledge
and red flags) via the shared scorecard, plus a light market-cap + coverage
pass. So a gem here says exactly what the desk says about the same name.

Guardrails (non-negotiable):
  · Quality-first — anything with a forensic red flag, promoter pledge, a
    suspect model or a LOW-confidence valuation is excluded, no exceptions.
  · No hype — "multibagger" describes the setup (quality × growth × low coverage
    × a fair price), not a prediction. The screen can legitimately return zero.
  · No leverage, no position sizing, no personalised advice — it lists
    candidates and reasons; what to do with them is the reader's call.

Result cached in KVStore, keyed to the evidence build it was computed from.
"""
from __future__ import annotations
import datetime as _dt
import logging

from app import models
from app.scorecard import build_scorecard, red_flags, green_flags
from app.manager_engine import load_evidence

log = logging.getLogger("hidden_gems")

GEMS_KEY = "manager:hidden_gems:v2"

# ── screen thresholds (honest, quality-first) ────────────────────────────────
CAP_MAX_CR  = 20000.0   # small / mid only — a large cap is not "under-followed"
CAP_MIN_CR  = 300.0     # floor: below this liquidity/quality noise dominates
QUAL_MIN    = 60        # forensic accounting-quality composite floor (clean books)
ROE_MIN     = 0.14      # durable returns on capital
GROWTH_MIN  = 0.10      # revenue CAGR …
PAT_YOY_MIN = 0.12      # … or recent PAT growth — a real compounding engine
MOS_FLOOR   = -0.15     # not meaningfully above our own fair value
TOP_N       = 12


def _frac(x):
    """Normalise a ratio to a fraction: 0.18 stays 0.18, 18 → 0.18."""
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x / 100.0 if abs(x) > 1.5 else x


def _clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def _cap_tier(cap_cr):
    if cap_cr is None:
        return None
    if cap_cr < 5000:
        return "Small cap"
    if cap_cr < 20000:
        return "Mid cap"
    return "Large cap"


def _pct(x, d=0):
    return None if x is None else round(x * 100, d)


def _thesis(ev, sc, cap_cr, roe, g, pat_yoy, covered):
    """Honest, evidence-derived reasons this name has multibagger ingredients."""
    out = []
    tier = _cap_tier(cap_cr) or "Small/mid cap"
    if not covered:
        out.append(f"Under-followed: {tier.lower()} at Rs {cap_cr:,.0f} Cr with no street "
                   f"consensus yet — the kind of gap where mispricing lives.")
    else:
        out.append(f"Small base: {tier.lower()} at Rs {cap_cr:,.0f} Cr — room to keep "
                   f"compounding, and less picked-over than the large caps.")
    q = (ev.get("quality") or {}).get("composite")
    if q is not None:
        out.append(f"Clean, high-quality books — forensic score {q:.0f}/100, no red flags "
                   f"and no promoter pledge.")
    if roe is not None:
        out.append(f"Durable returns on capital — ROE {roe*100:.0f}%.")
    gbits = []
    if g is not None and g >= GROWTH_MIN:
        gbits.append(f"revenue compounding {g*100:.0f}%")
    if pat_yoy is not None and pat_yoy >= PAT_YOY_MIN:
        gbits.append(f"PAT up {pat_yoy*100:.0f}% YoY")
    if gbits:
        out.append("Growth engine: " + " · ".join(gbits) + ".")
    mos = (ev.get("model") or {}).get("mos")
    if mos is not None and mos > 0.05:
        out.append(f"And it's not priced for it — +{mos*100:.0f}% margin of safety to our "
                   f"independent fair value.")
    elif mos is not None:
        out.append("Priced fairly rather than cheap — the case rests on the durable growth, "
                   "not a valuation gap.")
    # positive flow / momentum, straight from the shared green-flag logic
    for gf in green_flags(ev):
        if any(k in gf.lower() for k in ("institutions added", "uptrend", "profit up",
                                         "confident management")):
            out.append(gf + ".")
    # dedupe, keep order
    seen, dedup = set(), []
    for s in out:
        if s.lower() not in seen:
            seen.add(s.lower()); dedup.append(s)
    return dedup[:6]


_RISK_BOILERPLATE = [
    "Small- and mid-caps carry thinner liquidity and larger drawdowns — size any position accordingly.",
    "“Multibagger” describes the setup (quality × growth × low coverage × a fair price), not a promise. Theses break.",
    "Educational research, not investment advice — verify every figure and do your own diligence.",
]


def _rank(ev, sc, roe, g, pat_yoy, cap_cr, covered):
    """A 0-100 conviction blend for names that already cleared the hard gates."""
    scores = sc.get("scores") or {}
    def sv(k):
        return (scores.get(k) or {}).get("value")
    base = 0.0
    parts = [("quality", 0.30), ("growth", 0.20), ("valuation", 0.18),
             ("safety", 0.12), ("momentum", 0.10)]
    wsum = 0.0
    for k, w in parts:
        v = sv(k)
        if v is not None:
            base += w * v; wsum += w
    score = (base / wsum) if wsum else 50.0
    # bonuses — capped so no single factor dominates
    if roe is not None:
        score += min(10.0, max(0.0, (roe - ROE_MIN) * 60))       # higher ROE
    if not covered:
        score += 8.0                                             # genuinely uncovered
    elif cap_cr is not None and cap_cr < 5000:
        score += 4.0                                             # small cap = under-followed
    gmax = max([x for x in (g, pat_yoy) if x is not None] or [0])
    score += min(8.0, max(0.0, (gmax - GROWTH_MIN) * 30))        # stronger growth
    a = ev.get("alpha")
    if a is not None:
        score += min(5.0, max(-5.0, (a - 50) * 0.1))
    return round(_clamp(score), 1)


def evaluate_name(tk, ev, cap_cr, roe, g, pat_yoy, covered, pe=None) -> dict | None:
    """Apply the hard gates to one name and, if it passes, build its gem record.
    Pure (no DB) so it's unit-testable. Returns None when a gate fails."""
    sc = build_scorecard(ev)
    if not sc.get("available"):
        return None
    model = ev.get("model") or {}
    mos = model.get("mos")
    conf = (model.get("confidence") or "").upper()

    # ── HARD GATES (honesty first; any failure excludes the name) ─────────────
    if cap_cr is None or cap_cr > CAP_MAX_CR or cap_cr < CAP_MIN_CR:
        return None
    if (sc.get("scores", {}).get("quality", {}) or {}).get("value") is None:
        return None
    if ((ev.get("quality") or {}).get("composite") or 0) < QUAL_MIN:
        return None
    if red_flags(ev):                       # any forensic / pledge / governance / news flag
        return None
    if sc.get("valuation_suspect") or conf == "LOW":
        return None
    if roe is None or roe < ROE_MIN:
        return None
    has_growth = (g is not None and g >= GROWTH_MIN) or \
                 (pat_yoy is not None and pat_yoy >= PAT_YOY_MIN)
    if not has_growth:
        return None
    if mos is not None and mos < MOS_FLOOR:
        return None

    return {
        "ticker": tk,
        "name": ev.get("name"),
        "sector": ev.get("sector"),
        "price": ev.get("price"),
        "market_cap_cr": round(cap_cr, 0),
        "cap_tier": _cap_tier(cap_cr),
        "score": _rank(ev, sc, roe, g, pat_yoy, cap_cr, covered),
        "grade": sc.get("grade"),
        "overall": sc.get("overall"),
        "roe_pct": _pct(roe, 1),
        "rev_cagr_pct": _pct(g, 1),
        "pat_yoy_pct": _pct(pat_yoy, 0),
        "mos_pct": _pct(mos, 1),
        "pe": round(pe, 1) if pe else None,
        "under_followed": not covered,
        "thesis": _thesis(ev, sc, cap_cr, roe, g, pat_yoy, covered),
        "risks": _RISK_BOILERPLATE,
        "scores": sc.get("scores"),
    }


def find_hidden_gems(db, limit: int = TOP_N, refresh: bool = False) -> dict:
    """Screen the universe for under-followed quality compounders. Cached to the
    evidence build it was computed from; returns available=False with a clear
    reason when the nightly evidence isn't ready."""
    ev_blob = load_evidence(db)
    if not ev_blob or not ev_blob.get("names"):
        return {"available": False,
                "message": "The Fund Manager evidence build hasn't run yet — hidden gems "
                           "populate on the next nightly refresh."}
    as_of = ev_blob.get("as_of")

    if not refresh:
        from app.manager_engine import _kv_get
        cached = _kv_get(db, GEMS_KEY)
        if cached and cached.get("evidence_as_of") == as_of:
            return {**cached, "cached": True}

    names = ev_blob["names"]

    # light DB pass: shares (→ market cap) and ROE / P/E from the valuation table
    id_ticker, shares_by = {}, {}
    for cid, tk, sh in db.query(models.Company.id, models.Company.ticker,
                                models.Company.shares_outstanding).all():
        id_ticker[cid] = tk
        shares_by[tk] = sh
    val_by = {}
    for cid, roe, pe in db.query(models.Valuation.company_id, models.Valuation.roe,
                                 models.Valuation.pe).all():
        tk = id_ticker.get(cid)
        if tk:
            val_by[tk] = {"roe": roe, "pe": pe}

    gems, considered = [], 0
    for tk, ev in names.items():
        if not build_scorecard(ev).get("available"):
            continue
        considered += 1
        price = ev.get("price")
        shares = shares_by.get(tk)
        cap_cr = (price * shares) if (price and shares) else None
        vinfo = val_by.get(tk) or {}
        roe = _frac(vinfo.get("roe"))
        g = _frac(ev.get("growth"))
        pat_yoy = _frac((ev.get("results") or {}).get("pat_yoy"))
        covered = bool((ev.get("consensus") or {}).get("rating") or
                       (ev.get("consensus") or {}).get("target"))
        gem = evaluate_name(tk, ev, cap_cr, roe, g, pat_yoy, covered, pe=vinfo.get("pe"))
        if gem:
            gems.append(gem)

    gems.sort(key=lambda x: -(x["score"] or 0))
    gems = gems[:max(1, min(limit, 30))]

    result = {
        "available": True,
        "as_of": as_of,
        "evidence_as_of": as_of,
        "universe_considered": considered,
        "n_found": len(gems),
        "gems": gems,
        "criteria": {
            "market_cap_cr": [CAP_MIN_CR, CAP_MAX_CR],
            "min_quality": QUAL_MIN, "min_roe_pct": ROE_MIN * 100,
            "min_growth_pct": GROWTH_MIN * 100, "min_pat_yoy_pct": PAT_YOY_MIN * 100,
            "clean_forensics": True, "excludes_suspect_models": True,
        },
        "note": ("Under-followed small/mid-caps with clean books, durable ROE, a real growth "
                 "engine and a price that isn't already discounting the story. Ingredients of "
                 "a multibagger — potential, not a promise. Educational, not advice."),
        "cached": False,
    }
    try:
        from app.manager_engine import _kv_put
        _kv_put(db, GEMS_KEY, result)
    except Exception as e:
        log.warning(f"hidden-gems cache write failed: {type(e).__name__}: {e}")
    return result
