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
IIP = "index_of_industrial_production"
USDINR = "exchange_rate_of_indian_rupee_vis_vis_us_dollar_month_end"
FX_RESERVES = "foreign_exchange_reserves_us_million"
M3 = "m3"
BANK_CREDIT = "bank_credit_crore"
TRADE_BAL = "foreign_trade_balance_total_us_million"
FPI_NET = "net_portfolio_investment_us_million"
GDP_NOMINAL = "gdp_at_market_prices_current"
GDP_REAL = "gdp_at_market_prices_constant"


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


def _yoy(pts):
    d1, v1 = _latest(pts)
    if v1 is None:
        return None
    cutoff = (_dt.date.fromisoformat(d1) - _dt.timedelta(days=366)).isoformat()
    d0, v0 = _asof(pts, cutoff)
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
    iy = _yoy(block(IIP))
    if iy is not None:
        out["iip_yoy"] = {"pct": round(iy * 100, 2), "as_of": _latest(block(IIP))[0]}

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


def write_overlay(db, updates: dict[str, dict]) -> int:
    """Merge {slug: {name, freq, points:[[iso,val],…]}} into the KV overlay.
    Returns points written. Used by API fetchers and the admin xlsx upload."""
    from app import models
    row = db.query(models.KVStore).filter_by(key=UPDATES_KEY).first()
    cur = (row.value or {}).get("series", {}) if row else {}
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
    else:
        db.add(models.KVStore(key=UPDATES_KEY, value=payload))
    db.commit()
    return n
