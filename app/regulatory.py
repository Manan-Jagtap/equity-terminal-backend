"""
app/regulatory.py — official regulatory feed tracker (RBI + SEBI).

Pulls the regulators' OWN RSS feeds (public, machine-readable, no scraping of
third-party monitors):

  · RBI press releases    https://rbi.org.in/pressreleases_rss.xml
  · RBI notifications     https://rbi.org.in/notifications_rss.xml
  · SEBI updates          https://www.sebi.gov.in/sebirss.xml

Each item is keyword-tagged by the market surface it touches (rates/liquidity,
F&O & market structure, disclosure rules, IPO/fundraising, mutual funds,
banking supervision …) so the Economy page can show "what changed that could
affect stocks" — titles + links to the regulator's page, never reproduced
content. Stored in KVStore "regulatory_feed_v1" (last ~120 items), refreshed
daily by the scheduler. Fail-silent: a feed that's down changes nothing.
"""
from __future__ import annotations

import datetime as _dt
import logging
import re
import xml.etree.ElementTree as ET

import requests

log = logging.getLogger("regulatory")

FEED_KEY = "regulatory_feed_v1"

FEEDS = [
    ("RBI", "press", "https://rbi.org.in/pressreleases_rss.xml"),
    ("RBI", "notification", "https://rbi.org.in/notifications_rss.xml"),
    ("SEBI", "update", "https://www.sebi.gov.in/sebirss.xml"),
    # PIB (Press Information Bureau) — English press feed. Broad govt news, so
    # PIB items are kept only when they hit an economic/market keyword (below).
    ("PIB", "press", "https://www.pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3&reg=48"),
]

# PIB is all-of-government; keep only market-relevant items. RBI/SEBI are
# inherently regulatory so everything they publish is kept.
_ECON_KEEP = ("gst", "tax", "budget", "fiscal", "disinvest", "psu", "ipo",
              "trade", "export", "import", "inflation", "cpi", "wpi", "iip",
              "industrial production", "index of", "manufacturing", "pli ",
              "production linked", "repo", "rbi", "sebi", "fdi", "fpi",
              "gdp", "economy", "economic", "revenue", "capex", "infrastructure",
              "banking", "credit", "monetary", "coal", "steel", "power", "auto",
              "e-way", "core sector", "eight core")

# tag → keywords (matched on lowercase title). Order matters: first hit wins
# the primary tag; all hits are recorded.
TAGS = [
    ("rates & liquidity", ("repo", "crr", "slr", "monetary policy", "mpc",
                           "liquidity", "omo", "vrr", "vrrr", "standing deposit")),
    ("fx & external", ("forex", "foreign exchange", "fema", "ecb", "fpi",
                       "fdi", "remittance", "libor", "rupee")),
    ("market structure", ("f&o", "futures", "options", "derivative", "margin",
                          "circuit", "market making", "algo", "hft",
                          "settlement", "t+1", "t+0", "short sell")),
    ("disclosure & governance", ("disclosure", "lodr", "listing obligations",
                                 "insider", "related party", "pledge",
                                 "takeover", "sast", "buyback", "delisting")),
    ("ipo & fundraising", ("ipo", "public issue", "rights issue", "qip",
                           "preferential", "sme exchange", "drhp")),
    ("mutual funds & amcs", ("mutual fund", "amc", "nav", "expense ratio",
                             "debt fund", "elss")),
    ("banking supervision", ("bank licence", "prompt corrective", "pca",
                             "nbfc", "asset quality", "provisioning", "basel",
                             "capital adequacy", "kyc", "aml")),
    ("enforcement", ("penalty", "enforcement", "adjudication", "settlement order",
                     "debar", "fraud", "show cause", "investigation")),
]


def _tag(title: str) -> list[str]:
    t = title.lower()
    return [name for name, kws in TAGS if any(k in t for k in kws)]


def _parse_rss(xml_text: str, source: str, kind: str) -> list[dict]:
    out = []
    try:
        # strip BOM + control chars the RBI feed sometimes carries
        xml_text = re.sub(r"^[^<]*<", "<", xml_text, count=1)
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        log.warning(f"{source}/{kind} RSS parse failed: {e}")
        return out
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        # description — stripped of HTML; used only to extract the GST figure
        # (never rendered in the UI, which shows the title + a link to source).
        desc = re.sub(r"<[^>]+>", " ", item.findtext("description") or "")
        desc = re.sub(r"\s+", " ", desc).strip()[:400]
        if not (title and link):
            continue
        date = None
        if pub:
            try:
                from email.utils import parsedate_to_datetime
                date = parsedate_to_datetime(pub).date().isoformat()
            except (TypeError, ValueError):
                for fmt in ("%d %b %Y", "%Y-%m-%d"):
                    try:
                        date = _dt.datetime.strptime(pub, fmt).date().isoformat()
                        break
                    except ValueError:
                        continue
        out.append({"source": source, "kind": kind, "title": title[:220],
                    "link": link, "date": date, "tags": _tag(title),
                    "desc": desc if source == "PIB" else None})
    return out


def fetch_feeds() -> list[dict]:
    items: list[dict] = []
    for source, kind, url in FEEDS:
        try:
            r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200:
                # regulators serve UTF-8 without a charset header; requests
                # guesses latin-1 and mangles em-dashes — decode explicitly
                items.extend(_parse_rss(r.content.decode("utf-8", "replace"),
                                        source, kind))
            else:
                log.warning(f"{source}/{kind}: HTTP {r.status_code}")
        except Exception as e:
            log.warning(f"{source}/{kind}: {type(e).__name__}: {e}")
    return items


def refresh(db) -> dict:
    """Merge freshly fetched items into the KV feed (dedup by link), keep the
    newest ~120, and return a summary."""
    from app import models
    fresh = fetch_feeds()
    # PIB is all-of-government: keep only economically-relevant items so the
    # radar stays about "what changed that could move stocks", not general news.
    fresh = [it for it in fresh if it["source"] != "PIB"
             or any(k in (it["title"] or "").lower() for k in _ECON_KEEP)]
    row = db.query(models.KVStore).filter_by(key=FEED_KEY).first()
    cur = {i["link"]: i for i in ((row.value or {}).get("items") or [])} if row else {}
    added = 0
    today = _dt.date.today().isoformat()
    for it in fresh:
        prev = cur.get(it["link"])
        if prev is None:
            added += 1
        # SEBI's feed carries no pubDate — stamp when WE first saw the item so
        # ordering is stable and honest ("first seen", not a claimed pub date).
        it["first_seen"] = (prev or {}).get("first_seen") or today
        cur[it["link"]] = it
    items = sorted(cur.values(),
                   key=lambda x: (x.get("date") or x.get("first_seen") or ""),
                   reverse=True)[:120]
    payload = {"items": items,
               "updated_at": _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"}
    if row:
        row.value = payload
    else:
        db.add(models.KVStore(key=FEED_KEY, value=payload))
    db.commit()
    gst = {}
    try:
        gst = extract_gst(db)      # pull the monthly GST figure if PIB carried it
    except Exception as e:
        log.warning(f"GST extract: {type(e).__name__}: {e}")
    log.info(f"regulatory feed: {len(fresh)} fetched, {added} new, {len(items)} kept; {gst}")
    return {"fetched": len(fresh), "new": added, "kept": len(items), **gst}


def load(db) -> dict:
    from app import models
    row = db.query(models.KVStore).filter_by(key=FEED_KEY).first()
    return row.value if row else {"items": [], "updated_at": None}


# ── GST collection extraction from PIB ───────────────────────────────────────
_MONTHS = {m.lower(): i for i, m in enumerate(
    ["", "January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"])}


def _to_crore(num: float, unit: str) -> float | None:
    u = unit.lower()
    if "lakh crore" in u or "trillion" in u:
        return num * 1e5
    if "crore" in u:
        return num
    return None


def extract_gst(db) -> dict:
    """Scan the PIB feed for the monthly 'GST revenue collection' release and
    pull the gross figure into the macro overlay (gst_gross_collections_cr).
    Best-effort and fail-silent: the release text is only in the title/desc, so
    a phrasing change simply yields nothing (the card stays 'awaiting feed').
    Never fabricates a number."""
    import calendar
    import re as _re
    from app import macro_data
    data = load(db)
    pat = _re.compile(
        r"gst.{0,40}?(?:collection|revenue).{0,60}?"
        r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)\s*(lakh crore|crore|trillion)",
        _re.I)
    mon = _re.compile(r"\b(" + "|".join(list(_MONTHS)[1:]) + r")[,\s]+(\d{4})", _re.I)
    wrote = 0
    for it in data.get("items", []):
        if it.get("source") != "PIB":
            continue
        blob = (it.get("title") or "") + " " + (it.get("desc") or "")
        if "gst" not in blob.lower():
            continue
        m = pat.search(blob)
        if not m:
            continue
        try:
            num = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        cr = _to_crore(num, m.group(2))
        if cr is None:
            continue
        mm = mon.search(blob)
        if mm:
            y, mo = int(mm.group(2)), _MONTHS[mm.group(1).lower()]
        else:
            d0 = _dt.date.fromisoformat((it.get("date") or _dt.date.today().isoformat())[:10])
            # the release usually reports the PRIOR month
            prev = (d0.replace(day=1) - _dt.timedelta(days=1))
            y, mo = prev.year, prev.month
        iso = _dt.date(y, mo, calendar.monthrange(y, mo)[1]).isoformat()
        wrote += macro_data.write_overlay(db, {macro_data.GST_COLLECTIONS: {
            "name": "GST gross collections (₹ cr)", "freq": "M",
            "points": [[iso, cr]]}})
    return {"gst_points": wrote}
