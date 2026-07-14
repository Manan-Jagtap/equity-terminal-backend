"""
app/macro_sources.py — ongoing refresh for the macro store.

Sources, all optional and key-gated (the seed from the owner's RBI DBIE
export carries the engine until a key goes live; nothing here ever blocks):

  · TradingEconomics — TRADINGECONOMICS_KEY env var ("user:secret" format).
    Level series that align with our DBIE slugs (USDINR, 10Y yield, repo,
    FX reserves) merge straight into them; rate-type series (CPI YoY) land
    in their own te_* slugs so index math is never polluted.
  · MoSPI — MOSPI_KEY + MOSPI_CPI_URL env vars. Their portal issues keys on
    registration and documents exact endpoints in its Swagger UI; the URL is
    configurable so no endpoint guessing is baked in.
  · Admin xlsx re-upload — the same two DBIE export formats the seed came
    from, parsed by ingest_dbie_xlsx below (POST /api/admin/macro/upload).

Owner sets all keys on Railway directly — never in chat, never in the repo.
"""
from __future__ import annotations

import calendar
import datetime as _dt
import logging
import os
import re

import requests

from app import macro_data

log = logging.getLogger("macro_sources")

TE_BASE = "https://api.tradingeconomics.com"

# TE indicator → (our slug, kind). "level" merges into the DBIE slug;
# "own" gets a te_* slug of its own.
TE_MAP = {
    "currency": (macro_data.USDINR, "level"),          # USDINR spot
    "government bond 10y": (macro_data.GSEC_10Y, "level"),
    "interest rate": (macro_data.REPO, "level"),
    "foreign exchange reserves": (macro_data.FX_RESERVES, "level"),
    "inflation rate": ("te_cpi_yoy_pct", "own"),
    "gdp annual growth rate": ("te_gdp_yoy_pct", "own"),
}


def fetch_tradingeconomics(db) -> int:
    key = os.getenv("TRADINGECONOMICS_KEY", "").strip()
    if not key:
        return 0
    wrote = 0
    since = (_dt.date.today() - _dt.timedelta(days=400)).isoformat()
    for indicator, (slug, _kind) in TE_MAP.items():
        try:
            r = requests.get(
                f"{TE_BASE}/historical/country/india/indicator/{indicator}/{since}",
                params={"c": key, "format": "json"}, timeout=20)
            if r.status_code != 200:
                continue
            pts = []
            for row in r.json() or []:
                d = str(row.get("DateTime") or "")[:10]
                v = row.get("Value")
                if d and v is not None:
                    pts.append([d, float(v)])
            if pts:
                wrote += macro_data.write_overlay(
                    db, {slug: {"name": f"TE {indicator}", "freq": "D", "points": pts}})
        except Exception as e:
            log.warning(f"TE fetch {indicator}: {type(e).__name__}: {e}")
    return wrote


def fetch_mospi(db) -> int:
    """MoSPI CPI refresh. Endpoint differs per portal version, so it's fully
    env-configured: MOSPI_CPI_URL (complete GET url; {key} placeholder allowed)
    + MOSPI_KEY. Response handled for the common {data:[{...Month/Year/Index}]}
    shapes; anything unrecognized is logged and skipped, never guessed."""
    key = os.getenv("MOSPI_KEY", "").strip()
    url = os.getenv("MOSPI_CPI_URL", "").strip()
    if not (key and url):
        return 0
    try:
        r = requests.get(url.replace("{key}", key), timeout=20,
                         headers={"x-api-key": key})
        if r.status_code != 200:
            log.warning(f"MoSPI: HTTP {r.status_code}")
            return 0
        body = r.json()
        rows = body if isinstance(body, list) else \
            (body.get("data") or body.get("Data") or body.get("records") or [])
        MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
        pts = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            y = row.get("Year") or row.get("year")
            m = row.get("Month") or row.get("month")
            v = (row.get("Index") or row.get("index") or row.get("CombinedIndex")
                 or row.get("Value") or row.get("value"))
            if not (y and m and v is not None):
                continue
            mi = MONTHS.get(str(m).lower()) or (int(m) if str(m).isdigit() else None)
            if not mi:
                continue
            d = _dt.date(int(y), mi, calendar.monthrange(int(y), mi)[1])
            try:
                pts.append([d.isoformat(), float(v)])
            except (TypeError, ValueError):
                continue
        if pts:
            return macro_data.write_overlay(
                db, {macro_data.CPI_2024: {"name": "MoSPI CPI", "freq": "M",
                                           "points": sorted(pts)}})
    except Exception as e:
        log.warning(f"MoSPI fetch: {type(e).__name__}: {e}")
    return 0


# High-frequency "activity" indicators (GST, PMI, power, e-way bills, auto,
# UPI) — the value a BCG-style monitor adds over the RBI statistical release.
# We source them from the PRIMARY publishers, never a third-party monitor.
# There is no single free keyless API for these, so each is env-configured:
#   ACTIVITY_<SLUG>_URL returns JSON [{date, value}] (or {data:[...]}), with an
#   optional ACTIVITY_<SLUG>_KEY sent as x-api-key. Absent → skipped, and the
#   dashboard shows the indicator as "awaiting source" with the publisher named.
# This keeps a launch honest: a number appears only when a real feed backs it.
_ACTIVITY_ENV = {
    macro_data.GST_COLLECTIONS: "GST",
    macro_data.EWAY_BILLS:      "EWAY",
    macro_data.PMI_MFG:         "PMI_MFG",
    macro_data.PMI_SVC:         "PMI_SVC",
    macro_data.POWER_DEMAND:    "POWER",
    macro_data.AUTO_SALES:      "AUTO",
    macro_data.UPI_TXN:         "UPI",
}


def fetch_activity(db) -> int:
    """Pull any activity indicator whose ACTIVITY_<X>_URL env var is set.
    Response: a list of {date/period, value} objects (or {data:[…]}). Points
    are date-normalised via the same parser the DBIE uploader uses."""
    wrote = 0
    for slug, env in _ACTIVITY_ENV.items():
        url = os.getenv(f"ACTIVITY_{env}_URL", "").strip()
        if not url:
            continue
        key = os.getenv(f"ACTIVITY_{env}_KEY", "").strip()
        try:
            r = requests.get(url.replace("{key}", key), timeout=20,
                             headers={"x-api-key": key} if key else {})
            if r.status_code != 200:
                log.warning(f"activity {slug}: HTTP {r.status_code}")
                continue
            body = r.json()
            rows = body if isinstance(body, list) else \
                (body.get("data") or body.get("records") or [])
            pts = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                d = _parse_date(row.get("date") or row.get("period")
                                or row.get("month") or row.get("Date"))
                v = (row.get("value") if row.get("value") is not None
                     else row.get("Value"))
                if d is not None and v is not None:
                    try:
                        pts.append([d.isoformat(), float(str(v).replace(",", ""))])
                    except ValueError:
                        continue
            if pts:
                name = macro_data.ACTIVITY_META.get(slug, (slug,))[0]
                wrote += macro_data.write_overlay(
                    db, {slug: {"name": name, "freq": "M", "points": sorted(pts)}})
        except Exception as e:
            log.warning(f"activity {slug}: {type(e).__name__}: {e}")
    return wrote


def refresh_all(db) -> dict:
    te = fetch_tradingeconomics(db)
    mo = fetch_mospi(db)
    act = fetch_activity(db)
    configured_act = [env.lower() for slug, env in _ACTIVITY_ENV.items()
                      if os.getenv(f"ACTIVITY_{env}_URL", "").strip()]
    return {"tradingeconomics_points": te, "mospi_points": mo,
            "activity_points": act,
            "keys": {"tradingeconomics": bool(os.getenv("TRADINGECONOMICS_KEY", "").strip()),
                     "mospi": bool(os.getenv("MOSPI_KEY", "").strip()),
                     "activity_sources": configured_act}}


# ── DBIE xlsx ingest (admin re-upload path) ─────────────────────────────────

def _slug(h):
    s = re.sub(r"[^a-z0-9]+", "_", str(h).lower()).strip("_")
    return re.sub(r"_+", "_", s)


def _parse_date(d):
    if isinstance(d, _dt.datetime):
        return d.date()
    if isinstance(d, _dt.date):
        return d
    s = str(d).strip()
    for fmt in ("%d-%b-%Y", "%b-%Y"):
        try:
            dt = _dt.datetime.strptime(s, fmt).date()
            if fmt == "%b-%Y":
                dt = dt.replace(day=calendar.monthrange(dt.year, dt.month)[1])
            return dt
        except ValueError:
            pass
    m = re.match(r"^(\d{4})-Q([1-4])$", s)
    if m:
        y, mth = int(m.group(1)), int(m.group(2)) * 3
        return _dt.date(y, mth, calendar.monthrange(y, mth)[1])
    try:
        return _dt.date.fromisoformat(s[:10])
    except ValueError:
        return None


def ingest_dbie_xlsx(db, file_obj) -> dict:
    """Parse an RBI DBIE macro export (the same format the seed came from)
    and merge every series into the overlay. Returns a summary."""
    import openpyxl
    FREQ = {"Daily": "D", "Weekly": "W", "Fortnightly": "F",
            "Monthly": "M", "Quarterly": "Q"}
    wb = openpyxl.load_workbook(file_obj, data_only=True)   # full width, not read_only
    updates: dict = {}
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        hi = next((i for i, r in enumerate(rows)
                   if sum(c is not None for c in r) > 2), None)
        if hi is None:
            continue
        hdr = [str(c).replace("\n", " ").strip() if c is not None else None
               for c in rows[hi]]
        try:
            dcol = next(j for j, h in enumerate(hdr) if h)
        except StopIteration:
            continue
        for j, h in enumerate(hdr):
            if j <= dcol or not h:
                continue
            sl = _slug(h)
            pts = []
            for r in rows[hi + 1:]:
                d = _parse_date(r[dcol]) if dcol < len(r) and r[dcol] is not None else None
                v = r[j] if j < len(r) else None
                if d is None or v is None:
                    continue
                try:
                    pts.append([d.isoformat(), float(str(v).replace(",", ""))])
                except ValueError:
                    continue
            if len(pts) >= 4:
                updates[sl] = {"name": h, "freq": FREQ.get(ws.title, "?"),
                               "points": sorted(pts)}
    n = macro_data.write_overlay(db, updates) if updates else 0
    return {"series": len(updates), "points_added": n}
