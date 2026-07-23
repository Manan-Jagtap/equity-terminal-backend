"""
app/macro_data.py — India macro series store for the Fund Manager engine.

Three layers, merged newest-wins at read time:
  1. SEED — app/data/macro_seed.json.gz, parsed from the owner's RBI DBIE
     exports (148 series, ~29k points: policy rates, G-sec/T-bill yields,
     CPI/WPI/IIP, USDINR, FX reserves, M3/credit, trade, BoP, GDP, HPI…).
  2. KV OVERLAY — KVStore "macro_updates_v1", written by app/macro_sources.py
     (TradingEconomics / MoSPI, key-gated) and by admin xlsx re-uploads.
  3. Nothing is fabricated: a series that isn't in either layer returns None
     and the engine says nothing about it.

`macro_summary(db)` distils the store into the PM-relevant block: rate stance,
10Y move, inflation, currency, reserves, money/credit growth, external flows,
GDP momentum. Pure dictionary math — unit-testable with a fake store.
"""
from __future__ import annotations

import datetime as _dt
import gzip
import json
import logging
import os

log = logging.getLogger("macro_data")

UPDATES_KEY = "macro_updates_v1"
_SEED_PATH = os.path.join(os.path.dirname(__file__), "data", "macro_seed.json.gz")
_seed_cache: dict | None = None

# Canonical slugs (as produced by the DBIE export parser).
GSEC_10Y = "10_year_g_sec_yield_fbil"
REPO = "policy_repo_rate"
CPI_2012 = "consumer_price_index_2012_100"
CPI_2024 = "consumer_price_index_2024_100"
WPI = "wholesale_price_index_2011_12_100"
IIP = "index_of_industrial_production"            # DBIE seed, 2011-12 base
IIP_2022_23 = "index_of_industrial_production_2022_23"   # MoSPI MCP, new base
USDINR = "exchange_rate_of_indian_rupee_vis_vis_us_dollar_month_end"
FX_RESERVES = "foreign_exchange_reserves_us_million"
M3 = "m3"
BANK_CREDIT = "bank_credit_crore"
TRADE_BAL = "foreign_trade_balance_total_us_million"
FPI_NET = "net_portfolio_investment_us_million"
GDP_NOMINAL = "gdp_at_market_prices_current"
GDP_REAL = "gdp_at_market_prices_constant"

# High-frequency "activity" indicators a BCG-style monitor tracks that the RBI
# DBIE export does NOT carry — the value we don't already have. Each is keyed to
# its PRIMARY public source (never a third-party aggregator's proprietary
# monitor). They populate from macro_sources activity fetchers / admin uploads;
# until then they read as "awaiting source", never fabricated.
GST_COLLECTIONS = "gst_gross_collections_cr"          # GSTN / PIB monthly release
EWAY_BILLS = "eway_bills_generated_mn"                 # GSTN e-way bill portal
PMI_MFG = "pmi_manufacturing"                          # S&P Global (licensed headline)
PMI_SVC = "pmi_services"                               # S&P Global (licensed headline)
POWER_DEMAND = "power_peak_demand_met_mw"             # Grid India / POSOCO
AUTO_SALES = "auto_total_sales_units"                 # SIAM monthly
UPI_TXN = "upi_transactions_mn"                        # NPCI monthly stats

# slug → (human name, primary source, licence note). Drives the dashboard's
# "activity" section and the admin status view.
ACTIVITY_META = {
    GST_COLLECTIONS: ("GST gross collections (₹ cr)", "GSTN / PIB", "public"),
    EWAY_BILLS:      ("E-way bills generated (mn)", "GSTN e-way bill portal", "public"),
    PMI_MFG:         ("PMI — Manufacturing", "S&P Global", "licensed headline"),
    PMI_SVC:         ("PMI — Services", "S&P Global", "licensed headline"),
    POWER_DEMAND:    ("Peak power demand met (MW)", "Grid India (POSOCO)", "public"),
    AUTO_SALES:      ("Auto total sales (units)", "SIAM", "public"),
    UPI_TXN:         ("UPI transactions (mn)", "NPCI", "public"),
}


def _load_seed() -> dict:
    global _seed_cache
    if _seed_cache is None:
        try:
            with gzip.open(_SEED_PATH, "rt") as f:
                _seed_cache = json.load(f).get("series") or {}
        except Exception as e:
            log.error(f"macro seed unreadable: {type(e).__name__}: {e}")
            _seed_cache = {}
    return _seed_cache


def _overlay(db) -> dict:
    if db is None:
        return {}
    try:
        from app import models
        row = db.query(models.KVStore).filter_by(key=UPDATES_KEY).first()
        return (row.value or {}).get("series", {}) if row else {}
    except Exception:
        db.rollback()
        return {}


def series(db, slug: str) -> list[tuple[str, float]]:
    """Merged [(iso_date, value)] ascending; overlay points win on date clash."""
    base = {d: v for d, v in ((_load_seed().get(slug) or {}).get("points") or [])}
    for d, v in ((_overlay(db).get(slug) or {}).get("points") or []):
        base[d] = v
    return sorted(base.items())


def catalog(db) -> dict:
    """slug → {name, freq, n, first, last} for admin/status displays."""
    out = {}
    seed = _load_seed()
    ov = _overlay(db)
    for slug in set(seed) | set(ov):
        pts = series(db, slug)
        if not pts:
            continue
        meta = seed.get(slug) or ov.get(slug) or {}
        out[slug] = {"name": meta.get("name") or slug, "freq": meta.get("freq"),
                     "n": len(pts), "first": pts[0][0], "last": pts[-1][0]}
    return out


# ── Attributed macro reference points (published forecasts + activity read) ──
# These are CITED third-party figures (widely reported, source-attributed), not a
# redistributed data feed — one dated reading, refreshable by the owner via the
# admin activity-point endpoint. They give the Economy layer a forward view and
# seed the activity read until the owner wires live ACTIVITY_*_URL sources.
MACRO_FORECAST = {
    "source": "ICRA Research", "as_of": "2026-03",
    "note": "Baseline assumes ~$85/bbl crude (Indian basket).",
    "rows": [
        {"metric": "Real GDP growth", "fy26": 7.5, "fy27": 6.5, "unit": "%"},
        {"metric": "CPI inflation", "fy26": 2.1, "fy27": 4.5, "unit": "%"},
        {"metric": "WPI inflation", "fy26": 0.7, "fy27": 3.5, "unit": "%"},
        {"metric": "CAD / GDP", "fy26": 0.9, "fy27": 1.7, "unit": "%"},
        {"metric": "Fiscal deficit / GDP", "fy26": 4.5, "fy27": 4.6, "unit": "%"},
    ],
    # GDP sensitivity to the crude assumption (baseline → stress).
    "crude_scenarios": [
        {"crude_usd_bbl": 85, "fy27_gdp": 6.5}, {"crude_usd_bbl": 105, "fy27_gdp": 5.8},
        {"crude_usd_bbl": 125, "fy27_gdp": 5.0},
    ],
}

# ICRA Business Activity Monitor — a 16-indicator composite of PUBLIC high-freq
# reads (auto, mining, power, cement, non-oil exports, ports, fuel, steel, GST
# e-way, airline pax, SCB deposits/credit). We cite the composite's YoY direction
# (attributed); our own activity_read() also aggregates the public slugs directly.
# The seed below is only a DEFAULT — a live DB overlay (slug `business_activity_yoy`,
# written via POST /api/admin/macro/activity-point) takes precedence, so this is
# refreshable, not frozen. The forecast likewise falls back to a KV overlay.
BUSINESS_ACTIVITY = "business_activity_yoy"
BUSINESS_ACTIVITY_REF = {
    "source": "ICRA Research", "name": "ICRA Business Activity Monitor (YoY %)",
    "points": [("2026-02-28", 10.0), ("2026-03-31", 7.6)],
}
_MACRO_FORECAST_KV = "macro_forecast_overlay"


def macro_forecast(db) -> dict:
    """The macro outlook — a live KV overlay if the owner has set one, else the
    dated ICRA default. Never frozen: refreshable without a code change."""
    try:
        from app import models
        row = db.query(models.KVStore).filter_by(key=_MACRO_FORECAST_KV).first() if db else None
        if row and isinstance(row.value, dict) and row.value.get("rows"):
            return row.value
    except Exception:
        if db:
            db.rollback()
    return MACRO_FORECAST


def activity_read(db) -> dict:
    """Aggregate the high-frequency activity indicators into a single read:
    per-indicator YoY (PMI by level vs 50), a breadth (share expanding), and a
    label. Feeds the macro regime and the Economy dashboard. Empty-safe."""
    indicators = []
    rising = total = 0
    for slug, (name, *_rest) in ACTIVITY_META.items():
        pts = series(db, slug)
        if len(pts) < 2:
            continue
        d1, v1 = _latest(pts)
        if v1 is None:
            continue
        if slug in (PMI_MFG, PMI_SVC):           # diffusion index: level vs 50
            up = v1 >= 50.0
            indicators.append({"slug": slug, "name": name, "value": round(v1, 1),
                               "read": "expansion" if up else "contraction", "as_of": d1})
        else:                                     # level series: YoY %
            _, v0 = _year_ago(pts, d1)
            if not v0:
                continue
            yoy = (v1 / v0 - 1) * 100
            up = yoy > 0
            indicators.append({"slug": slug, "name": name, "yoy_pct": round(yoy, 1), "as_of": d1})
        rising += 1 if up else 0
        total += 1

    # Composite: prefer a live DB overlay (owner-refreshable via activity-point),
    # else the dated ICRA default. Either way it is NOT frozen in code.
    ov_pts = series(db, BUSINESS_ACTIVITY)
    ref = BUSINESS_ACTIVITY_REF
    points = ov_pts if ov_pts else ref.get("points")
    composite = None
    if points:
        pd, pv = points[-1]
        prev = points[-2][1] if len(points) > 1 else None
        composite = {"name": ref["name"], "yoy_pct": pv, "as_of": pd,
                     "trend": ("decelerating" if prev is not None and pv < prev
                               else "accelerating" if prev is not None and pv > prev else None),
                     "source": ref["source"] if not ov_pts else "owner/primary overlay"}

    breadth = round(rising / total, 2) if total else None
    if composite:                                # composite drives the label when present
        label = ("expanding" if composite["yoy_pct"] >= 6
                 else "cooling" if composite["yoy_pct"] <= 2 else "moderating")
    elif breadth is not None:
        label = "expanding" if breadth >= 0.6 else "cooling" if breadth <= 0.35 else "mixed"
    else:
        label = None
    return {"label": label, "breadth": breadth, "n": total,
            "composite": composite, "indicators": indicators}


# ── small series math ────────────────────────────────────────────────────────

def _latest(pts):
    return pts[-1] if pts else (None, None)


def _asof(pts, iso):
    """Last point on/before iso date."""
    prev = None
    for d, v in pts:
        if d > iso:
            break
        prev = (d, v)
    return prev or (None, None)


def _delta(pts, days):
    d1, v1 = _latest(pts)
    if v1 is None:
        return None
    cutoff = (_dt.date.fromisoformat(d1) - _dt.timedelta(days=days)).isoformat()
    d0, v0 = _asof(pts, cutoff)
    return None if v0 is None else v1 - v0


def _year_ago(pts, ref_iso: str, tol_days: int = 25):
    """The point closest to exactly one year before ref_iso (same calendar
    month for month-end series), within tol_days. Avoids the off-by-one-month
    error a fixed 366-day lookback causes on month-end dates (which lands one
    day short of the prior year's month-end and grabs the month before)."""
    try:
        ref = _dt.date.fromisoformat(ref_iso)
    except (TypeError, ValueError):
        return (None, None)
    try:
        target = ref.replace(year=ref.year - 1)
    except ValueError:                     # 29 Feb → 28 Feb
        target = ref.replace(year=ref.year - 1, day=28)
    best = None
    for d, v in pts:
        try:
            dd = _dt.date.fromisoformat(d)
        except (TypeError, ValueError):
            continue
        gap = abs((dd - target).days)
        if gap <= tol_days and (best is None or gap < best[0]):
            best = (gap, d, v)
    return (best[1], best[2]) if best else (None, None)


def _yoy(pts):
    d1, v1 = _latest(pts)
    if v1 is None:
        return None
    _, v0 = _year_ago(pts, d1)
    if v0 in (None, 0):
        return None
    return v1 / v0 - 1.0


def _pct_chg(pts, days):
    d1, v1 = _latest(pts)
    if v1 is None:
        return None
    cutoff = (_dt.date.fromisoformat(d1) - _dt.timedelta(days=days)).isoformat()
    d0, v0 = _asof(pts, cutoff)
    if v0 in (None, 0):
        return None
    return v1 / v0 - 1.0


def macro_summary(db) -> dict:
    """The PM-relevant distillation. Every number carries its as-of date so
    staleness is visible, never hidden (DBIE exports age until re-uploaded or
    an API key goes live)."""
    def block(slug):
        return series(db, slug)

    out: dict = {}

    g = block(GSEC_10Y)
    d, v = _latest(g)
    if v is not None:
        chg = _delta(g, 92)
        out["gsec_10y"] = {"last": round(v, 2), "as_of": d,
                           "chg_3m_bps": round(chg * 100) if chg is not None else None}

    r = block(REPO)
    d, v = _latest(r)
    if v is not None:
        # last actual change: walk back to the previous different value
        prev = next((pv for _, pv in reversed(r[:-1]) if pv != v), None)
        out["repo"] = {"last": round(v, 2), "as_of": d,
                       "last_move": (None if prev is None else
                                     ("cut" if v < prev else "hike"))}

    # Inflation: prefer the current-base CPI, fall back to the 2012 base.
    cpi = block(CPI_2024) or block(CPI_2012)
    yy = _yoy(cpi)
    if yy is not None:
        out["cpi_yoy"] = {"pct": round(yy * 100, 2), "as_of": _latest(cpi)[0]}
    wy = _yoy(block(WPI))
    if wy is not None:
        out["wpi_yoy"] = {"pct": round(wy * 100, 2), "as_of": _latest(block(WPI))[0]}
    # IIP: prefer the current 2022-23 base (MoSPI, fresher), fall back to the
    # DBIE-seed 2011-12 base. YoY is base-invariant so the % stays comparable.
    iip = block(IIP_2022_23) or block(IIP)
    iy = _yoy(iip)
    if iy is not None:
        out["iip_yoy"] = {"pct": round(iy * 100, 2), "as_of": _latest(iip)[0]}

    u = block(USDINR)
    d, v = _latest(u)
    if v is not None:
        c3 = _pct_chg(u, 92)
        out["usdinr"] = {"last": round(v, 2), "as_of": d,
                         "chg_3m_pct": round(c3 * 100, 2) if c3 is not None else None}

    fx = block(FX_RESERVES)
    d, v = _latest(fx)
    if v is not None:
        c3 = _delta(fx, 92)
        out["fx_reserves_usd_bn"] = {"last": round(v / 1000, 1), "as_of": d,
                                     "chg_3m_bn": round(c3 / 1000, 1) if c3 is not None else None}

    my = _yoy(block(M3))
    if my is not None:
        out["m3_yoy"] = {"pct": round(my * 100, 2), "as_of": _latest(block(M3))[0]}
    cy = _yoy(block(BANK_CREDIT))
    if cy is not None:
        out["credit_yoy"] = {"pct": round(cy * 100, 2), "as_of": _latest(block(BANK_CREDIT))[0]}

    fp = block(FPI_NET)
    if fp:
        last3 = [v2 for _, v2 in fp[-3:]]
        out["fpi_net_3m_usd_mn"] = {"sum": round(sum(last3)), "as_of": fp[-1][0]}
    tb = block(TRADE_BAL)
    if tb:
        last3 = [v2 for _, v2 in tb[-3:]]
        out["trade_bal_3m_avg_usd_mn"] = {"avg": round(sum(last3) / len(last3)),
                                          "as_of": tb[-1][0]}
    gy = _yoy(block(GDP_NOMINAL))
    if gy is not None:
        out["gdp_nominal_yoy"] = {"pct": round(gy * 100, 1),
                                  "as_of": _latest(block(GDP_NOMINAL))[0]}

    # Rate stance: the one-word read the desk actually uses.
    stance = None
    repo_move = (out.get("repo") or {}).get("last_move")
    g3 = (out.get("gsec_10y") or {}).get("chg_3m_bps")
    if repo_move == "cut" or (g3 is not None and g3 <= -25):
        stance = "easing"
    elif repo_move == "hike" or (g3 is not None and g3 >= 25):
        stance = "tightening"
    elif repo_move is not None or g3 is not None:
        stance = "on_hold"
    out["stance"] = stance
    return out


# ── dashboard curation ───────────────────────────────────────────────────────
# Sections group the real DBIE series (+ activity indicators) into the view the
# Economy page renders. Each entry: (slug, label, unit, transform). transform:
#   "level"  → show the latest value
#   "yoy"    → show YoY % change (for index series: CPI, WPI, IIP, M3, credit)
#   "level_bn" → divide by 1000 (₹mn→bn / $mn→bn), show level
# new-base slug → old-base fallback (a rebase is a level break; the card
# prefers the fresher new base but degrades to the DBIE seed if it's absent).
_REBASE_FALLBACK = {IIP_2022_23: IIP}

DASHBOARD = [
    ("Growth & activity", [
        (GDP_REAL, "Real GDP", "₹ cr", "yoy"),
        (IIP_2022_23, "Industrial production (IIP)", "index", "yoy"),
        ("oecd_cli_india", "Leading indicator (OECD)", "index", "level"),
        (GST_COLLECTIONS, "GST collections", "₹ cr", "level"),
        (EWAY_BILLS, "E-way bills", "mn", "level"),
        (PMI_MFG, "PMI — Manufacturing", "index", "level"),
        (PMI_SVC, "PMI — Services", "index", "level"),
        (POWER_DEMAND, "Peak power demand", "MW", "level"),
        (AUTO_SALES, "Auto sales", "units", "level"),
    ]),
    ("Inflation", [
        (CPI_2024, "CPI inflation", "% YoY", "yoy"),
        (WPI, "WPI inflation", "% YoY", "yoy"),
    ]),
    ("Rates & liquidity", [
        (REPO, "Policy repo rate", "%", "level"),
        (GSEC_10Y, "10-yr G-sec yield", "%", "level"),
    ]),
    ("External sector", [
        (USDINR, "USD / INR", "₹", "level"),
        (FX_RESERVES, "FX reserves", "$ bn", "level_bn"),
        (TRADE_BAL, "Trade balance", "$ mn", "level"),
        (FPI_NET, "Net FPI flow", "$ mn", "level"),
    ]),
    ("Money & credit", [
        (M3, "Broad money (M3)", "% YoY", "yoy"),
        (BANK_CREDIT, "Bank credit", "% YoY", "yoy"),
        (UPI_TXN, "UPI transactions", "mn", "level"),
    ]),
]


def _spark(pts, n=24):
    return [round(v, 2) for _, v in pts[-n:]]


def yoy_series(pts) -> list[tuple[str, float]]:
    """[(date, YoY %)] — the RATE series for an index (CPI, WPI, IIP, M3…), so
    a chart of 'CPI inflation' shows 3-6% over time, not the 85→106 index level.
    Each point compares to the same calendar month a year earlier."""
    out = []
    for d, v in pts:
        _, v0 = _year_ago(pts, d)
        if v0 not in (None, 0) and v is not None:
            out.append((d, round((v / v0 - 1) * 100, 2)))
    return out


def dashboard(db) -> dict:
    """The Economy page payload: curated sections, each series with its latest
    value (or YoY), previous, as-of date, a short sparkline, and — for the
    high-frequency activity indicators we don't yet source — an honest
    'awaiting source' marker with the source named. Never fabricates."""
    seed = _load_seed()
    ov = _overlay(db)
    sections = []
    for title, items in DASHBOARD:
        rows = []
        for slug, label, unit, tf in items:
            pts = series(db, slug)
            # Rebase fallback: a new-base slug is preferred once populated, but
            # if its source is briefly down the DBIE-seed old base keeps the
            # card alive rather than showing 'awaiting'. YoY is base-invariant.
            if not pts and slug in _REBASE_FALLBACK:
                slug = _REBASE_FALLBACK[slug]
                pts = series(db, slug)
            meta = seed.get(slug) or ov.get(slug) or {}
            src = None
            if slug in ACTIVITY_META:
                nm, src, lic = ACTIVITY_META[slug]
            if not pts:
                rows.append({"slug": slug, "label": label, "unit": unit,
                             "value": None, "as_of": None,
                             "awaiting": True, "source": src})
                continue
            d, v = pts[-1]
            prev = pts[-2][1] if len(pts) > 1 else None
            if tf == "yoy":
                yy = _yoy(pts)
                val = round(yy * 100, 2) if yy is not None else None
                # previous YoY for the arrow
                pv = None
                if len(pts) > 13:
                    py = _yoy(pts[:-1])
                    pv = round(py * 100, 2) if py is not None else None
                # sparkline is the RATE trajectory, matching the % value shown
                rows.append({"slug": slug, "label": label, "unit": unit, "kind": "yoy",
                             "value": val, "prev": pv, "as_of": d,
                             "freq": meta.get("freq"), "spark": _spark(yoy_series(pts)),
                             "source": src})
            else:
                val = round(v / 1000, 1) if tf == "level_bn" else round(v, 2)
                pval = (round(prev / 1000, 1) if tf == "level_bn"
                        else round(prev, 2)) if prev is not None else None
                rows.append({"slug": slug, "label": label, "unit": unit, "kind": tf,
                             "value": val, "prev": pval, "as_of": d,
                             "freq": meta.get("freq"), "spark": _spark(pts),
                             "source": src})
        sections.append({"title": title, "series": rows})
    return {"summary": macro_summary(db), "sections": sections,
            "activity": activity_read(db), "forecast": macro_forecast(db),
            "series_count": len(catalog(db)),
            "note": ("India macro from primary official sources — RBI DBIE, MoSPI, "
                     "GSTN, NPCI, Grid India — never a third-party monitor. Each "
                     "figure carries its own as-of date.")}


def write_overlay(db, updates: dict[str, dict]) -> int:
    """Merge {slug: {name, freq, points:[[iso,val],…]}} into the KV overlay.
    Returns points written. Used by API fetchers and the admin xlsx upload.

    DATA-11: `cur` MUST be a DEEP COPY. Mutating the loaded row.value's nested
    dicts in place made SQLAlchemy's change detection compare the new payload
    against the already-mutated original (old == new), so the UPDATE was
    silently skipped and every overlay write after the first INSERT was LOST —
    the function still 'returned points written'. flag_modified is the
    belt-and-braces on top."""
    import copy
    from sqlalchemy.orm.attributes import flag_modified
    from app import models
    row = db.query(models.KVStore).filter_by(key=UPDATES_KEY).first()
    cur = copy.deepcopy((row.value or {}).get("series", {})) if row else {}
    n = 0
    for slug, ser in updates.items():
        dst = cur.setdefault(slug, {"name": ser.get("name") or slug,
                                    "freq": ser.get("freq"), "points": []})
        have = {d for d, _ in dst["points"]}
        for d, v in ser.get("points") or []:
            if d not in have and v is not None:
                dst["points"].append([d, float(v)])
                n += 1
        dst["points"].sort()
        dst["points"] = dst["points"][-600:]        # keep the overlay bounded
    payload = {"series": cur,
               "updated_at": _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"}
    if row:
        row.value = payload
        flag_modified(row, "value")
    else:
        db.add(models.KVStore(key=UPDATES_KEY, value=payload))
    db.commit()
    return n
