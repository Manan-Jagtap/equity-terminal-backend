"""
app/news_routes.py — Company news endpoint.

Source (merged, deduplicated, sorted newest-first, cached 30 min):
  1. IndianAPI  (INDIANAPI_KEY env var)  ← sole feed: the /stock payload's
     recentNews block, with a market-news mention filter as a last-resort fallback.

100% AI-free — the old opt-in Anthropic web_search enrichment was removed.
No Yahoo Finance, no Marketaux.

GET /api/companies/{ticker}/news
"""
from __future__ import annotations
import html as _html
import os, re, json, time
from datetime import datetime, timezone

import requests
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app import models

router = APIRouter(prefix="/api/companies")

_NEWS_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL  = 1800
_INDIANAPI_BASE = os.getenv("INDIANAPI_BASE", "https://dev.indianapi.in").rstrip("/")


# ── Source 1 (primary): IndianAPI /company_news ──────────────────────────────
def _parse_news_items(arts: list) -> list[dict]:
    items = []
    for a in (arts or [])[:25]:
        if not isinstance(a, dict):
            continue
        pub_raw = (a.get("published") or a.get("pub_date") or "").strip()
        ts, pub = 0, pub_raw
        # company_news: "Fri, 05 Jun 2026 22:16:58 IST"; market_news: ISO.
        for fmt in ("%a, %d %b %Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                dt = datetime.strptime(pub_raw.replace(" IST", "").strip()[:25], fmt)
                ts, pub = dt.timestamp(), dt.strftime("%Y-%m-%dT%H:%M:%S")
                break
            except Exception:
                continue
        items.append({
            "title":   _html.unescape(a.get("title", "") or ""),
            "source":  a.get("source", "IndianAPI"),
            "url":     a.get("article_link") or a.get("url", ""),
            "published": pub,
            "summary": a.get("summary", ""),
            "type":    "news",
            "_ts":     ts,
        })
    return items


def _recent_news_items(rn: list) -> list[dict]:
    """The production /stock payload's recentNews shape:
    {id, headline, date ('12 Jul 2026' / ISO), timeToRead, url, listimage}."""
    items = []
    for a in (rn or [])[:25]:
        if not isinstance(a, dict):
            continue
        title = _html.unescape((a.get("headline") or a.get("title") or "").strip())
        if not title:
            continue
        raw = str(a.get("date") or "").strip()
        ts, pub = 0, raw
        for fmt in ("%d %b %Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(raw[:19] if "T" in raw else raw, fmt)
                ts, pub = dt.timestamp(), dt.strftime("%Y-%m-%dT%H:%M:%S")
                break
            except Exception:
                continue
        items.append({"title": title, "source": a.get("source", "IndianAPI"),
                      "url": a.get("url", ""), "published": pub,
                      "summary": a.get("summary", ""), "type": "news", "_ts": ts})
    return items


def _indianapi_news(ticker: str, name: str = "") -> tuple[list[dict], str | None]:
    """IndianAPI company news. The production host has no /company_news and its
    /news endpoint ignores stock_name (general feed) — the COMPANY-specific
    stories live in the /stock payload's recentNews block. Try the ticker,
    then the full name, then a suffix-stripped name (M&M, BAJAJ-AUTO and some
    banks match by name where the symbol returns nothing)."""
    key = os.getenv("INDIANAPI_KEY", "").strip()
    if not key:
        return [], "no_key"

    cands = [ticker]
    nm = (name or "").strip()
    if nm and nm not in cands:
        cands.append(nm)
        stripped = re.sub(r"\s+(Ltd\.?|Limited|Industries|Corporation|Corp\.?|Company)\s*$", "", nm, flags=re.I).strip()
        if stripped and stripped not in cands:
            cands.append(stripped)

    last_err = "no_results"
    for q in cands:
        data = None
        try:
            from app import vendor_meter; vendor_meter.tick()  # FIX-07
            r = requests.get(_INDIANAPI_BASE + "/stock",
                             headers={"X-API-Key": key, "x-api-key": key},
                             params={"name": q}, timeout=25)
            if r.status_code == 200:
                data = r.json()
            else:
                last_err = f"http_{r.status_code}"
        except Exception as e:
            last_err = str(e)[:80]
        # OUTCOME per attempt (vendor_meter.record). The candidates are our
        # ticker, then the company's name, then its suffix-stripped name — the
        # later forms are KNOWN to miss for some names (that is why there are
        # three), so an empty answer is not the vendor being down: empty_ok.
        try:
            from app import vendor_meter as _vm
            _vm.record(_vm.payload_ok(data, empty_ok=True))
        except Exception:
            pass
        if data is None:
            continue
        arts = (data or {}).get("recentNews") if isinstance(data, dict) else None
        items = _recent_news_items(arts)
        if items:
            return items, None
    return [], last_err


_NAME_NOISE = re.compile(r"\b(ltd|limited|india|industries|company|corp|corporation|enterprises)\b\.?", re.I)


def _match_terms(ticker: str, name: str) -> list[str]:
    """Terms that actually identify the company. The old first-word substring
    match made "LIC" hit pub-LIC-sector / po-LIC-y articles, filling LIC
    Housing's News tab with Navy stories. Use the ticker plus the name's first
    TWO meaningful words, matched at word boundaries."""
    base = _NAME_NOISE.sub(" ", (name or "").lower())
    words = [w for w in re.split(r"[^a-z0-9&]+", base) if len(w) > 1]
    terms = [ticker.lower()]
    if len(words) >= 2:
        terms.append(f"{words[0]} {words[1]}")
    elif words:
        terms.append(words[0])
    return [t for t in terms if len(t) > 2]


def _mentions(text: str, terms: list[str]) -> bool:
    low = (text or "").lower()
    return any(re.search(r"(?<![a-z0-9])" + re.escape(t).replace(r"\ ", r"\s+") + r"(?![a-z0-9])", low)
               for t in terms)


def _market_news_for(ticker: str, name: str) -> tuple[list[dict], str | None]:
    """Fallback: scan general market news for any mention of the company, so the
    News tab is never empty even for low-coverage names."""
    key = os.getenv("INDIANAPI_KEY", "").strip()
    if not key:
        return [], "no_key"
    data, err = None, None
    try:
        # Metered and outcome-tracked like the primary lookup above. This
        # fallback was invisible to both: quota burned uncounted, failures unseen.
        from app import vendor_meter; vendor_meter.tick()
        r = requests.get(_INDIANAPI_BASE + "/news",
                         headers={"X-API-Key": key, "x-api-key": key},
                         params={"page_no": 1, "size": 50}, timeout=20)
        if r.status_code == 200:
            data = r.json()
        else:
            err = f"http_{r.status_code}"
    except Exception as e:
        err = str(e)[:80]
    # The general market feed is never legitimately empty.
    try:
        from app import vendor_meter as _vm
        _vm.record(_vm.payload_ok(data))
    except Exception:
        pass
    if err:
        return [], err
    arts = data if isinstance(data, list) else (data.get("news") or data.get("data") or [])
    terms = _match_terms(ticker, name)
    matched = [a for a in arts if isinstance(a, dict)
               and _mentions(str(a.get("title", "")) + " " + str(a.get("summary", "")), terms)]
    items = _parse_news_items(matched)
    return (items, None) if items else ([], "no_market_match")


# ── Merge + dedupe ───────────────────────────────────────────────────────────
def _merge(lists):
    seen, merged = set(), []
    for lst in lists:
        for item in lst:
            key = (item.get("title") or "")[:50].lower().strip()
            if key and key not in seen:
                seen.add(key)
                merged.append(item)
    def sk(x):
        if x.get("_ts"): return x["_ts"]
        try: return datetime.strptime((x.get("published") or "")[:19],"%Y-%m-%dT%H:%M:%S").timestamp()
        except Exception: return 0
    merged.sort(key=sk, reverse=True)
    for item in merged:
        item.pop("_ts", None)
    return merged[:25]


# ── Route ────────────────────────────────────────────────────────────────────
@router.get("/{ticker}/news")
def company_news(ticker: str, refresh: bool = False, db: Session = Depends(get_db)):
    ticker = ticker.upper()

    # Cache check (skip if refresh=true)
    if not refresh:
        cached = _NEWS_CACHE.get(ticker)
        if cached and time.time() - cached[0] < _CACHE_TTL:
            return cached[1]

    # `refresh=true` DELIBERATELY bypasses the cache, on an endpoint that takes
    # no authentication — so a loop over it spends ~3-4 IndianAPI calls per
    # request (_indianapi_news tries ticker → name → stripped name) with nothing
    # refusing them. The budget guard already exists (app/api_budget.py, used by
    # profile_routes and manager_engine); it simply was never asked here.
    #
    # Out of quota, a refresh degrades to whatever is cached rather than
    # spending past the ceiling. Fails OPEN: a guard that errors must not take
    # the news tab down.
    if refresh:
        _allowed = True
        try:
            from app import api_budget
            _allowed = not api_budget.would_exceed(db, 4)
        except Exception:
            _allowed = True
        if not _allowed:
            cached = _NEWS_CACHE.get(ticker)
            if cached:
                return cached[1]
            refresh = False   # nothing cached: fall through, but do not force a refetch

    co = db.query(models.Company).filter_by(ticker=ticker).first()
    if not co:
        raise HTTPException(404, f"Unknown ticker {ticker}")

    sources, all_items, debug = [], [], {}

    # Primary feed: IndianAPI company news (ticker → name → stripped name).
    ia_items, ia_err = _indianapi_news(ticker, co.name)
    if ia_items: all_items.append(ia_items); sources.append("indianapi")
    if ia_err:   debug["indianapi_err"] = ia_err

    # Last-resort fallback so the News tab is never empty: filter market news
    # for any mention of the company name / ticker.
    if not all_items:
        mkt, mkt_err = _market_news_for(ticker, co.name)
        if mkt: all_items.append(mkt); sources.append("market")
        if mkt_err: debug["market_err"] = mkt_err

    merged = _merge(all_items)
    # Cache a lexicon read of these headlines so the sentiment score gets a news
    # leg — zero extra API calls (piggybacks the fetch the user just triggered).
    if merged:
        try:
            from app.sentiment import record_news_sentiment
            record_news_sentiment(db, ticker, merged)
        except Exception:
            pass
    result = {"ticker":ticker,"name":co.name,"count":len(merged),
              "sources":sources,"cached":False,"items":merged,"debug":debug}
    _NEWS_CACHE[ticker] = (time.time(), {**result,"cached":True})
    return result
