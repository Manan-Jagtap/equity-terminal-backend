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
    # What this feed ACTUALLY carries (verified live 16 Aug 2026): the latest 20
    # releases as <title> + <link> only — no <description>, no <pubDate>. So PIB
    # rows have date=None (ordered by first_seen) and there is no body text to
    # mine. Titles + links is all it is good for. A "GST collections" figure
    # extractor used to run over this feed and never wrote a point in prod: the
    # Ministry of Finance stopped the monthly GST press release in Jul 2024
    # (figures now live on the GSTN portal), and the only titles the regex could
    # still match were cumulative FY/multi-month totals — which it would have
    # stamped as a monthly value ~10x too high. Removed; GST is manual-only
    # (admin activity-point / ACTIVITY_GST_URL), see macro_data.ACTIVITY_META.
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
                    "link": link, "date": date, "tags": _tag(title)})
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
    log.info(f"regulatory feed: {len(fresh)} fetched, {added} new, {len(items)} kept")
    return {"fetched": len(fresh), "new": added, "kept": len(items)}


def load(db) -> dict:
    from app import models
    row = db.query(models.KVStore).filter_by(key=FEED_KEY).first()
    return row.value if row else {"items": [], "updated_at": None}
