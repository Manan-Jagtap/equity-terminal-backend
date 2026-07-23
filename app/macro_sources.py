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
import json
import logging
import os
import re

import requests

from app import macro_data

log = logging.getLogger("macro_sources")

TE_BASE = "https://api.tradingeconomics.com"

# ── MoSPI MCP — the ministry's official, KEYLESS data server ────────────────
# https://mcp.mospi.gov.in speaks MCP (JSON-RPC 2.0 over HTTP + SSE framing).
# NO AI is involved on our side: we call the `get_data` tool exactly like any
# REST endpoint and parse JSON rows — MCP here is just the transport the
# ministry chose. MIT-licensed, no key, no registration (validated 23 Jul
# 2026: CPI base-2012 All-India 156 monthly rows; IIP 2022-23 base monthly;
# WPI headline index). This supersedes the key-gated MOSPI_CPI_URL fetcher
# below, which stays as a manual fallback. Kill-switch: MOSPI_MCP=0.
MOSPI_MCP_URL = os.getenv("MOSPI_MCP_URL", "https://mcp.mospi.gov.in")

_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}

# New-base slugs (a base change is a LEVEL BREAK — never append a new-base
# index into an old-base series; each base gets its own slug and the catalog
# picks new slugs up automatically). Canonical definition lives in macro_data
# (all slug constants do); re-exported here for the fetcher.
IIP_2022_23 = macro_data.IIP_2022_23


def _mcp_get_data(dataset: str, filters: dict, timeout: int = 45) -> dict | None:
    """One stateless `tools/call get_data` against the MoSPI MCP server.
    Returns the decoded data payload dict, or None on any failure (logged)."""
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "get_data",
                       "arguments": {"dataset": dataset, "filters": filters}}}
    try:
        r = requests.post(MOSPI_MCP_URL, json=body, timeout=timeout,
                          headers={"Accept": "application/json, text/event-stream"})
        r.raise_for_status()
        # SSE framing: the reply is one `data: {...}` line.
        payload = None
        for line in r.text.splitlines():
            if line.startswith("data: "):
                payload = json.loads(line[6:])
                break
        if payload is None:                      # plain-JSON fallback
            payload = r.json()
        content = ((payload.get("result") or {}).get("content") or [])
        text = content[0].get("text", "") if content else ""
        out = json.loads(text) if text else None
        if not isinstance(out, dict) or "data" not in out:
            log.warning("MoSPI MCP %s: no data in reply (%s)", dataset, str(out)[:120])
            return None
        return out
    except Exception as e:
        log.warning("MoSPI MCP %s failed: %s: %s", dataset, type(e).__name__, str(e)[:120])
        return None


# ── DBnomics — free global aggregator (IMF/WB/OECD…), the CROSS-CHECK leg ────
# "Data that argues with itself": the IMF's India CPI lands in its OWN slug
# (never merged into the MoSPI series) so the dashboard can show two
# independent sources of the same economy. Keyless; kill-switch DBNOMICS=0.
DBNOMICS_URL = os.getenv("DBNOMICS_URL", "https://api.db.nomics.world/v22")
DBN_IMF_CPI = "dbn_imf_cpi_2010_100"


def fetch_dbnomics(db) -> int:
    """IMF India CPI (monthly, 2010=100) via DBnomics into its own slug."""
    if os.getenv("DBNOMICS", "").strip() in ("0", "false", "no"):
        return 0
    try:
        r = requests.get(f"{DBNOMICS_URL}/series/IMF/CPI/M.IN.PCPI_IX",
                         params={"observations": 1}, timeout=30)
        r.raise_for_status()
        doc = ((r.json().get("series") or {}).get("docs") or [{}])[0]
        pts = []
        for period, val in zip(doc.get("period") or [], doc.get("value") or []):
            # DBnomics encodes missing observations as the string "NA" (not
            # null) — float("NA") would abort the whole series, so skip any
            # value that is null, "NA", or otherwise non-numeric per-point.
            if val is None or str(val).strip().upper() in ("NA", "") or len(str(period)) < 7:
                continue
            try:
                fv = float(val)
            except (TypeError, ValueError):
                continue
            y, m = int(str(period)[:4]), int(str(period)[5:7])
            pts.append([f"{y:04d}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}", fv])
        if not pts:
            log.warning("DBnomics: IMF India CPI came back empty")
            return 0
        return macro_data.write_overlay(db, {DBN_IMF_CPI: {
            "name": "CPI — IMF cross-check (2010=100)", "freq": "M",
            "points": sorted(pts)[-240:]}})
    except Exception as e:
        log.warning("DBnomics fetch failed: %s: %s", type(e).__name__, str(e)[:120])
        return 0


def _month_iso(year, month_name) -> str | None:
    """Month-END iso date — the DBIE seed's convention; using month-start here
    would store the same month twice under two dates."""
    m = _MONTHS.get(str(month_name or "").strip().lower())
    if not m or not year:
        return None
    return f"{int(year):04d}-{m:02d}-{calendar.monthrange(int(year), m)[1]:02d}"


def fetch_mospi_mcp(db) -> int:
    """Refresh CPI / IIP / WPI monthly points from the MoSPI MCP server into
    the overlay. Keyless; each series fails independently and silently keeps
    the last-good points (the DBIE seed + overlay history carries on)."""
    if os.getenv("MOSPI_MCP", "").strip() in ("0", "false", "no"):
        return 0
    updates: dict[str, dict] = {}

    # CPI, base 2012 (continues the DBIE CPI_2012 series; published to Dec-2025,
    # after which the 2024 base takes over). All-India (99) · General group (0)
    # · Combined sector (3).
    j = _mcp_get_data("CPI", {"base_year": "2012", "series": "Current",
                              "state_code": "99", "group_code": "0",
                              "sector_code": "3", "limit": "36"})
    if j:
        pts = []
        for r in j.get("data") or []:
            iso, v = _month_iso(r.get("year"), r.get("month")), r.get("index")
            if iso and v is not None:
                try:
                    pts.append([iso, float(v)])
                except (TypeError, ValueError):
                    pass
        if pts:
            updates[macro_data.CPI_2012] = {"name": "CPI (2012=100, All India)",
                                            "freq": "M", "points": sorted(pts)}

    # CPI, base 2024 (the current series, 2026+). Same grammar attempted; the
    # upstream occasionally 400s while the new base stabilises — skip honestly.
    j = _mcp_get_data("CPI", {"base_year": "2024", "series": "Current",
                              "state_code": "99", "group_code": "0",
                              "sector_code": "3", "limit": "36"})
    if j:
        pts = []
        for r in j.get("data") or []:
            iso, v = _month_iso(r.get("year"), r.get("month")), r.get("index")
            if iso and v is not None:
                try:
                    pts.append([iso, float(v)])
                except (TypeError, ValueError):
                    pass
        if pts:
            updates[macro_data.CPI_2024] = {"name": "CPI (2024=100, All India)",
                                            "freq": "M", "points": sorted(pts)}

    # IIP, base 2022-23 (NEW slug — never appended to the old-base IIP series).
    # type=General filters server-side to the headline row per month; the
    # client-side guard below stays as belt-and-braces.
    j = _mcp_get_data("IIP", {"frequency": "Monthly", "base_year": "2022-23",
                              "type": "General", "limit": "36"})
    if j:
        pts = []
        for r in j.get("data") or []:
            if (r.get("type") or "").strip() != "General":
                continue
            iso, v = _month_iso(r.get("year"), r.get("month")), r.get("index")
            if iso and v is not None:
                try:
                    pts.append([iso, float(v)])
                except (TypeError, ValueError):
                    pass
        if pts:
            updates[IIP_2022_23] = {"name": "IIP (2022-23=100)", "freq": "M",
                                    "points": sorted(pts)}

    # WPI headline (majorgroup == "Wholesale price index"). Rows are the full
    # commodity tree month-by-month; 200 rows ≈ the newest month, whose first
    # row is the headline — one fresh point per weekly run keeps the series
    # current on top of the DBIE seed.
    j = _mcp_get_data("WPI", {"limit": "200"})
    if j:
        pts = []
        for r in j.get("data") or []:
            if (r.get("majorgroup") or "").strip().lower() != "wholesale price index":
                continue
            iso, v = _month_iso(r.get("year"), r.get("month")), r.get("index_value")
            if iso and v is not None:
                try:
                    pts.append([iso, float(v)])
                except (TypeError, ValueError):
                    pass
        if pts:
            updates[macro_data.WPI] = {"name": "WPI (2011-12=100)", "freq": "M",
                                       "points": sorted(pts)}

    if not updates:
        log.warning("MoSPI MCP: no series parsed this run")
        return 0
    n = macro_data.write_overlay(db, updates)
    log.info("MoSPI MCP: wrote %d new points across %d series", n, len(updates))
    return n

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


# MoSPI / OGD series the owner can turn on by setting one env URL each. Each
# entry: env var (complete GET url; {key} placeholder allowed) → (macro slug,
# display name, frequency). The response parser handles the common government
# shapes ({data:[{Year,Month,Index/Value}]} and {records:[…]}); anything
# unrecognised is logged and skipped, never guessed. One key: MOSPI_KEY (or
# OGD_KEY as a fallback) registered once on the portal.
# Each series carries an expected INDEX-level band so a units mismatch — e.g. a
# CPI endpoint returning the inflation RATE (~5) under a "Value" key instead of
# the index (~105) — is rejected and logged, not silently merged into the index
# series (which then computes a nonsensical YoY, the old "index vs %" bug).
_GOV_SERIES = {
    "MOSPI_CPI_URL":  (macro_data.CPI_2024, "MoSPI CPI (2024=100)", "M", (80, 400)),
    "MOSPI_IIP_URL":  (macro_data.IIP, "MoSPI Index of Industrial Production", "M", (50, 400)),
    "MOSPI_GDP_URL":  (macro_data.GDP_NOMINAL, "MoSPI GDP (current prices)", "Q", (1000, None)),
    "OGD_WPI_URL":    (macro_data.WPI, "WPI (DPIIT via OGD)", "M", (50, 400)),
}

_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}


def _parse_gov_rows(rows, band=None, label="") -> list[list]:
    """[[iso_date, value], …] from the common MoSPI/OGD row shapes. Values outside
    the expected index band are dropped (and logged) so a rate-vs-index units
    mismatch can't poison the series."""
    lo, hi = (band or (None, None))
    pts, dropped = [], 0
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        v = next((row[k] for k in ("Index", "index", "CombinedIndex", "Value",
                                   "value", "wpi", "gdp") if row.get(k) is not None), None)
        if v is None:
            continue
        y = row.get("Year") or row.get("year")
        m = row.get("Month") or row.get("month")
        try:
            if y and m:
                mi = _MONTHS.get(str(m).lower()) or (int(m) if str(m).isdigit() else None)
                if not mi:
                    continue
                d = _dt.date(int(y), mi, calendar.monthrange(int(y), mi)[1])
            elif row.get("date") or row.get("Date"):
                d = _dt.date.fromisoformat(str(row.get("date") or row.get("Date"))[:10])
            else:
                continue
            fv = float(str(v).replace(",", ""))
            if (lo is not None and fv < lo) or (hi is not None and fv > hi):
                dropped += 1
                continue
            pts.append([d.isoformat(), fv])
        except (TypeError, ValueError):
            continue
    if dropped:
        log.warning("%s: dropped %d row(s) outside expected band %s — likely a "
                    "rate/index units mismatch in the feed", label or "gov feed", dropped, band)
    return sorted(pts)


def fetch_mospi(db) -> int:
    """Refresh every MoSPI/OGD series the owner has configured a URL for.
    Key from MOSPI_KEY (falls back to OGD_KEY). No-op until a key + at least
    one series URL are set; the RBI DBIE seed carries the engine meanwhile."""
    key = os.getenv("MOSPI_KEY", "").strip() or os.getenv("OGD_KEY", "").strip()
    if not key:
        return 0
    wrote = 0
    for env, (slug, name, freq, band) in _GOV_SERIES.items():
        url = os.getenv(env, "").strip()
        if not url:
            continue
        try:
            r = requests.get(url.replace("{key}", key), timeout=20,
                             headers={"x-api-key": key})
            if r.status_code != 200:
                log.warning(f"{env}: HTTP {r.status_code}")
                continue
            body = r.json()
            rows = body if isinstance(body, list) else \
                (body.get("data") or body.get("Data") or body.get("records") or [])
            pts = _parse_gov_rows(rows)
            if pts:
                wrote += macro_data.write_overlay(
                    db, {slug: {"name": name, "freq": freq, "points": pts}})
        except Exception as e:
            log.warning(f"{env} fetch: {type(e).__name__}: {e}")
    return wrote


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


OECD_CLI_URL = ("https://sdmx.oecd.org/public/rest/data/"
                "OECD.SDD.STES,DSD_STES@DF_CLI,/IND.M.LI...AA...H"
                "?startPeriod=2015-01&format=jsondata")


def fetch_oecd_cli(db) -> int:
    """India Composite Leading Indicator (OECD) — a normalised turning-point
    signal (100 = long-term trend; >100 & rising = above-trend and
    accelerating). Free, keyless SDMX-JSON API. → macro slug oecd_cli_india."""
    try:
        r = requests.get(OECD_CLI_URL, timeout=25,
                         headers={"Accept": "application/vnd.sdmx.data+json"})
        if r.status_code != 200:
            log.warning(f"OECD CLI: HTTP {r.status_code}")
            return 0
        d = r.json().get("data", {})
        structs = d.get("structures") or []
        # index → period from the observation dimension
        periods = []
        for x in (structs[0].get("dimensions", {}).get("observation", []) if structs else []):
            if x.get("id") == "TIME_PERIOD":
                periods = [v["id"] for v in x.get("values", [])]
        dsets = d.get("dataSets") or []
        series = (dsets[0].get("series") or {}) if dsets else {}
        if not (series and periods):
            return 0
        _key, ser = next(iter(series.items()))
        pts = []
        for idx, obs in (ser.get("observations") or {}).items():
            i = int(idx)
            if i < len(periods) and obs and obs[0] is not None:
                p = periods[i]                          # "YYYY-MM"
                try:
                    y, m = int(p[:4]), int(p[5:7])
                    import calendar
                    iso = _dt.date(y, m, calendar.monthrange(y, m)[1]).isoformat()
                    pts.append([iso, round(float(obs[0]), 3)])
                except (ValueError, IndexError):
                    continue
        if pts:
            return macro_data.write_overlay(db, {"oecd_cli_india": {
                "name": "OECD Composite Leading Indicator — India", "freq": "M",
                "points": sorted(pts)}})
    except Exception as e:
        log.warning(f"OECD CLI fetch: {type(e).__name__}: {e}")
    return 0


def refresh_all(db) -> dict:
    te = fetch_tradingeconomics(db)
    mcp = fetch_mospi_mcp(db)          # keyless official primary (CPI/IIP/WPI)
    dbn = fetch_dbnomics(db)           # IMF cross-check leg (own slug)
    mo = fetch_mospi(db)               # legacy key-gated fallback
    act = fetch_activity(db)
    cli = fetch_oecd_cli(db)
    configured_act = [env.lower() for slug, env in _ACTIVITY_ENV.items()
                      if os.getenv(f"ACTIVITY_{env}_URL", "").strip()]
    return {"tradingeconomics_points": te, "mospi_mcp_points": mcp, "dbnomics_points": dbn, "mospi_points": mo,
            "activity_points": act, "oecd_cli_points": cli,
            "keys": {"mospi_mcp": os.getenv("MOSPI_MCP", "").strip() not in ("0", "false", "no"),
                     "tradingeconomics": bool(os.getenv("TRADINGECONOMICS_KEY", "").strip()),
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
