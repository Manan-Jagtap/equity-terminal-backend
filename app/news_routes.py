"""
app/news_routes.py — Company news endpoint.

Two free sources, no API key needed:
  1. yfinance .news — headlines from ET, Moneycontrol, Bloomberg India, Reuters
  2. NSE corporate announcements via public JSON endpoint

GET /api/companies/{ticker}/news
Returns the 20 most recent news items, merged and deduplicated, sorted by date desc.

Response:
  {
    "ticker": "MUTHOOTFIN",
    "count": 20,
    "sources": ["yfinance", "nse"],
    "items": [
      {
        "title": "Muthoot Finance Q4FY26 PAT doubles to ₹3,349 Cr",
        "source": "Economic Times",
        "url": "https://...",
        "published": "2026-05-14T10:30:00",
        "summary": "...",
        "type": "news"          # news | announcement | result
      }
    ]
  }
"""
from __future__ import annotations
import os, time
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app import models

router = APIRouter(prefix="/api/companies")

# Simple in-process cache: {ticker: (timestamp, data)}
_NEWS_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 1800  # 30 minutes


def _yfinance_news(ticker: str) -> list[dict]:
    """Fetch news via yfinance (free, no key)."""
    try:
        import yfinance as yf
        yt = yf.Ticker(ticker + ".NS")
        raw = yt.news or []
        items = []
        for n in raw[:25]:
            # yfinance returns providerPublishTime as unix timestamp
            ts = n.get("providerPublishTime") or n.get("publishTime") or 0
            try:
                pub = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            except Exception:
                pub = None
            items.append({
                "title":     n.get("title", ""),
                "source":    n.get("publisher", ""),
                "url":       n.get("link", ""),
                "published": pub,
                "summary":   n.get("summary", ""),
                "type":      "news",
                "_ts":       ts,
            })
        return items
    except Exception:
        return []


def _nse_announcements(ticker: str) -> list[dict]:
    """
    Fetch corporate announcements from NSE's public JSON endpoint.
    No API key required — same data NSE website uses.
    """
    try:
        import urllib.request, json
        url = (
            f"https://www.nseindia.com/api/corp-info?symbol={ticker}"
            f"&market=equities&corpType=announcements"
        )
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; EquityTerminal/1.0)",
            "Accept": "application/json",
            "Referer": "https://www.nseindia.com/",
        }
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())

        items = []
        announcements = data.get("announcements", []) or data.get("data", [])
        for ann in announcements[:15]:
            # NSE returns dates as "21-May-2026" or "2026-05-21"
            raw_date = ann.get("bDt") or ann.get("an_dt") or ""
            try:
                for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y"):
                    try:
                        dt = datetime.strptime(raw_date.strip(), fmt)
                        raw_date = dt.strftime("%Y-%m-%dT00:00:00")
                        break
                    except Exception:
                        pass
            except Exception:
                pass

            items.append({
                "title":     ann.get("subject") or ann.get("desc", ""),
                "source":    "NSE Announcement",
                "url":       ann.get("attchmntFile") or f"https://www.nseindia.com/companies-listing/corporate-filings-announcements",
                "published": raw_date,
                "summary":   ann.get("desc", ""),
                "type":      "announcement",
                "_ts":       0,
            })
        return items
    except Exception:
        return []


def _bse_announcements(bse_code: str | None, ticker: str) -> list[dict]:
    """
    Fetch recent results/announcements from BSE.
    Uses BSE's public announcements API.
    """
    if not bse_code:
        return []
    try:
        import urllib.request, json
        url = f"https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w?strCat=-1&strPrevDate=&strScrip={bse_code}&strSearch=P&strToDate=&strType=C&subcategory=-1"
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; EquityTerminal/1.0)",
            "Accept": "application/json",
            "Referer": "https://www.bseindia.com/",
        }
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())

        items = []
        for ann in (data.get("Table", []) or [])[:10]:
            headline = ann.get("HEADLINE", "")
            dt_str   = ann.get("News_submission_dt", "")
            try:
                dt = datetime.strptime(dt_str[:19], "%Y-%m-%dT%H:%M:%S") if dt_str else None
                pub = dt.strftime("%Y-%m-%dT%H:%M:%S") if dt else None
            except Exception:
                pub = dt_str
            items.append({
                "title":     headline,
                "source":    "BSE Announcement",
                "url":       f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{ann.get('ATTACHMENTNAME','')}",
                "published": pub,
                "summary":   headline,
                "type":      "announcement",
                "_ts":       0,
            })
        return items
    except Exception:
        return []


@router.get("/{ticker}/news")
def company_news(ticker: str, db: Session = Depends(get_db)):
    """
    Fetch latest news and corporate announcements for the ticker.
    Merges yfinance news + NSE/BSE announcements.
    Results cached for 30 minutes.
    """
    ticker = ticker.upper()

    # Check cache
    cached = _NEWS_CACHE.get(ticker)
    if cached and time.time() - cached[0] < _CACHE_TTL:
        return cached[1]

    co = db.query(models.Company).filter_by(ticker=ticker).first()
    if not co:
        raise HTTPException(404, f"Unknown ticker {ticker}")

    # Gather from all sources in parallel would be ideal, but sequential is fine
    items: list[dict] = []
    sources_used: list[str] = []

    yf_items = _yfinance_news(ticker)
    if yf_items:
        items.extend(yf_items)
        sources_used.append("yfinance")

    nse_items = _nse_announcements(ticker)
    if nse_items:
        items.extend(nse_items)
        sources_used.append("nse")

    # Deduplicate by title similarity (first 40 chars)
    seen: set[str] = set()
    unique: list[dict] = []
    for item in items:
        key = (item.get("title") or "")[:40].lower().strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(item)

    # Sort by timestamp desc — yfinance items have _ts, announcements use 0
    def sort_key(x):
        if x.get("_ts"):
            return x["_ts"]
        pub = x.get("published") or ""
        try:
            return datetime.strptime(pub[:19], "%Y-%m-%dT%H:%M:%S").timestamp()
        except Exception:
            return 0

    unique.sort(key=sort_key, reverse=True)

    # Remove internal _ts before returning
    for item in unique:
        item.pop("_ts", None)

    result = {
        "ticker":   ticker,
        "name":     co.name,
        "count":    len(unique),
        "sources":  sources_used,
        "cached":   False,
        "items":    unique[:25],
    }

    _NEWS_CACHE[ticker] = (time.time(), {**result, "cached": True})
    return result
