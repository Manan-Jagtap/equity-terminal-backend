"""
app/baskets.py — thematic & smart-beta baskets.

Two families, both built from data we ALREADY compute (the multi-factor engine
in factors.py and the valuation-sector classifier) — no new model, no hand-picked
constituent lists:

  • SMART-BETA — factor-defined portfolios. Value, Quality, Momentum, Low-
    Volatility, Growth, and a blended Quality-at-a-Reasonable-Price. Members are
    the top slice of the visible universe on that factor's transparent 0-100
    rank, so membership is fully reproducible.

  • THEMATIC — rule-defined by valuation sector. Financials, Digital & IT,
    Consumption, Capex & Infrastructure, Commodities & Cyclicals, Healthcare,
    Auto, Materials & Chemicals. Membership is a published sector rule, never a
    curated list — the same classifier that drives the valuation engine.

Each basket carries its selection RULE, its constituents (ranked by Alpha), and
basket-level aggregates (count, median P/E · P/B · MoS, average ROE · Alpha, and
the average factor exposures) so the grouping is auditable. Descriptive current
snapshots — a research aid, NOT investment advice and NOT a backtest.
"""
from __future__ import annotations
from statistics import median, mean


# ── Smart-beta factor definitions (key in factors dict, label, one-line rule) ──
SMART_BETA = [
    ("value",    "Value",          "Cheapest slice — the best blend of margin-of-safety, earnings yield and book yield."),
    ("quality",  "Quality",        "Highest return-on-equity — capital-efficient compounders."),
    ("momentum", "Momentum",       "Strongest 12-minus-1-month price trend."),
    ("low_vol",  "Low Volatility", "Steadiest names — lowest realised 12-month volatility."),
    ("growth",   "Growth",         "Fastest expected forward growth."),
]

# ── Thematic membership rules, keyed on the engine's valuation sector ──────────
THEMES = [
    ("financials",  "Financials",             "Banks & NBFCs — geared to the rate cycle, credit growth and asset quality.",
        {"BANK", "NBFC", "INSURANCE"}),
    ("digital_it",  "Digital & IT",           "IT services & platforms — export-led, USD earners riding global tech spend.",
        {"IT_SERVICES"}),
    ("consumption", "Consumption",            "Staples & discretionary — domestic demand and brand pricing power.",
        {"CONSUMER", "CONSUMER_DISC"}),
    ("capex_infra", "Capex & Infrastructure", "Capital goods, construction, cement & defence — the domestic investment cycle.",
        {"CAPITAL_GOODS", "CONSTRUCTION", "CEMENT", "DEFENCE"}),
    ("commodities", "Commodities & Cyclicals","Metals, energy, sugar, paper & textiles — price-taker cyclicals.",
        {"METAL", "ENERGY", "SUGAR", "PAPER", "TEXTILES"}),
    ("healthcare",  "Healthcare",             "Pharma & hospitals — defensive, regulated, R&D-led.",
        {"PHARMA"}),
    ("auto",        "Auto & Mobility",        "OEMs & ancillaries — volume × mix × operating leverage.",
        {"AUTO"}),
    # MANUFACTURING is deliberately EXCLUDED — it is the classifier's residual
    # "couldn't place precisely" bucket (banks/shipping/etc. leak in), so theming
    # it would mislead. Materials is the clean chemicals + cables set only.
    ("materials",   "Materials & Chemicals",  "Chemicals & cables — specialty and commoditised industrial inputs.",
        {"CHEMICALS", "CABLES"}),
]

_FACTOR_LABELS = {"value": "Value", "quality": "Quality", "momentum": "Momentum",
                  "low_vol": "Low Vol", "growth": "Growth", "catalyst": "Revisions",
                  "surprise": "Surprise"}

# How many constituents a basket lists (also the smart-beta selection size).
_TOP_N = 15


def _num(xs):
    return [x for x in xs if isinstance(x, (int, float))]


def _member(r, factor_key=None):
    """Public constituent row — the fields the basket card needs."""
    return {
        "ticker": r.get("ticker"), "name": r.get("name"),
        "sector": r.get("sector"), "valuation_sector": r.get("valuation_sector"),
        "price": r.get("price"), "alpha_score": r.get("alpha_score"),
        "verdict": r.get("verdict"), "confidence": r.get("confidence"),
        "mos": r.get("mos"), "pe": r.get("pe"), "pb": r.get("pb"), "roe": r.get("roe"),
        # the factor this basket selects on (None for thematic → Alpha-ranked)
        "factor_score": (r.get("factors") or {}).get(factor_key) if factor_key else None,
    }


def _agg(members):
    """Basket-level aggregates + average factor exposures. All robust to gaps."""
    pes  = _num([m["pe"]  for m in members])
    pbs  = _num([m["pb"]  for m in members])
    roes = _num([m["roe"] for m in members])
    mos  = _num([m["mos"] for m in members])
    alp  = _num([m["alpha_score"] for m in members])
    exposures = {}
    for k in ("value", "quality", "momentum", "low_vol", "growth"):
        vals = _num([(m.get("_factors") or {}).get(k) for m in members])
        if vals:
            exposures[k] = round(mean(vals), 0)
    return {
        "count": len(members),
        "median_pe":  round(median(pes), 1) if pes else None,
        "median_pb":  round(median(pbs), 2) if pbs else None,
        "avg_roe":    round(mean(roes) * 100, 1) if roes else None,  # roe stored as fraction → %
        "median_mos": round(median(mos), 3) if mos else None,      # fraction
        "avg_alpha":  round(mean(alp), 1) if alp else None,
        "exposures":  exposures,
    }


def _finalize(members):
    """Attach aggregates, then drop the private _factors helper from members."""
    ag = _agg(members)
    for m in members:
        m.pop("_factors", None)
    return ag


def smart_beta_baskets(ranked: list[dict]) -> list[dict]:
    """Factor-tilted baskets: the top slice of the universe on each factor's
    transparent rank, plus a blended Quality-at-a-Reasonable-Price."""
    out = []
    for key, label, rule in SMART_BETA:
        pool = [r for r in ranked if (r.get("factors") or {}).get(key) is not None]
        pool.sort(key=lambda r: r["factors"][key], reverse=True)
        picks = pool[:_TOP_N]
        if not picks:          # a factor with no coverage yet — skip, don't show an empty card
            continue
        members = []
        for r in picks:
            m = _member(r, key)
            m["_factors"] = r.get("factors")
            members.append(m)
        ag = _finalize(members)
        out.append({
            "id": key, "name": label, "kind": "smart_beta",
            "factor": _FACTOR_LABELS.get(key, key),
            "rule": rule, "aggregates": ag, "constituents": members,
        })

    # Quality-at-a-Reasonable-Price: equal blend of quality and value ranks.
    pool = [r for r in ranked
            if (r.get("factors") or {}).get("quality") is not None
            and (r.get("factors") or {}).get("value") is not None]
    pool.sort(key=lambda r: 0.5 * r["factors"]["quality"] + 0.5 * r["factors"]["value"], reverse=True)
    picks = pool[:_TOP_N]
    members = []
    for r in picks:
        m = _member(r, None)
        m["factor_score"] = round(0.5 * r["factors"]["quality"] + 0.5 * r["factors"]["value"], 1)
        m["_factors"] = r.get("factors")
        members.append(m)
    ag = _finalize(members)
    out.append({
        "id": "qarp", "name": "Quality at a Reasonable Price", "kind": "smart_beta",
        "factor": "Quality × Value",
        "rule": "Equal blend of the Quality (ROE) and Value ranks — compounders that are not expensive.",
        "aggregates": ag, "constituents": members,
    })
    return out


def thematic_baskets(ranked: list[dict]) -> list[dict]:
    """Sector-rule baskets — every visible name whose valuation sector matches
    the theme, ranked by Alpha. Membership is the published classifier, so it is
    fully reproducible."""
    out = []
    for tid, label, rule, sectors in THEMES:
        pool = [r for r in ranked if (r.get("valuation_sector") or "").upper() in sectors]
        if not pool:
            continue
        pool.sort(key=lambda r: (r.get("alpha_score") is not None, r.get("alpha_score") or 0.0), reverse=True)
        members = []
        for r in pool[:_TOP_N]:
            m = _member(r, None)
            m["_factors"] = r.get("factors")
            members.append(m)
        # aggregate over the WHOLE theme, not just the listed top slice
        full = []
        for r in pool:
            m = _member(r, None)
            m["_factors"] = r.get("factors")
            full.append(m)
        ag = _agg(full)
        for m in members:
            m.pop("_factors", None)
        out.append({
            "id": tid, "name": label, "kind": "thematic",
            "sectors": sorted(sectors),
            "rule": rule, "aggregates": ag, "constituents": members,
        })
    return out


def all_baskets(ranked: list[dict]) -> dict:
    return {
        "smart_beta": smart_beta_baskets(ranked),
        "thematic": thematic_baskets(ranked),
        "universe": len(ranked),
        "note": ("Smart-beta baskets hold the top slice of the visible universe on a single "
                 "transparent factor; thematic baskets hold every name matching a published "
                 "valuation-sector rule. Descriptive current snapshots — a research aid, not "
                 "investment advice and not a backtest."),
    }
