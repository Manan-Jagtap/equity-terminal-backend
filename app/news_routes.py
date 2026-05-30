"""
app/news_routes.py — Company news endpoint.

Priority chain (results merged):
  1. Marketaux API   (if MARKETAUX_API_KEY set) — structured Indian market news
  2. Anthropic web_search (if ANTHROPIC_API_KEY set) — Claude searches live web
  3. yfinance fallback (free, limited for Indian tickers)

Results merged, deduplicated, sorted newest-first, cached 30 min.

GET /api/companies/{ticker}/news
"""
from __future__ import annotations
import os, json, time, ssl, urllib.request
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app import models

router = APIRouter(prefix="/api/companies")

_NEWS_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL  = 1800
_HAIKU      = "claude-haiku-4-5"


# ── Source 1: Marketaux ──────────────────────────────────────────────────────
def _marketaux_news(ticker: str, api_key: str) -> list[dict]:
    if not api_key:
        return []
    ctx = ssl.create_default_context()
    for sym in [f"NSE:{ticker}", ticker]:
        url = (f"https://api.marketaux.com/v1/news/all"
               f"?symbols={sym}&filter_entities=true&language=en"
               f"&limit=15&api_token={api_key}")
        req = urllib.request.Request(url, headers={"User-Agent":"EquityTerminal/1.0"})
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
                data = json.loads(r.read())
            articles = data.get("data", [])
            if not articles:
                continue
            items = []
            for a in articles:
                pub = a.get("published_at","")
                try:
                    dt  = datetime.fromisoformat(pub.replace("Z","+00:00"))
                    pub = dt.strftime("%Y-%m-%dT%H:%M:%S")
                    ts  = dt.timestamp()
                except Exception:
                    ts = 0
                items.append({"title":a.get("title",""),"source":a.get("source","Marketaux"),
                               "url":a.get("url",""),"published":pub,
                               "summary":a.get("description",""),"type":"news","_ts":ts})
            if items:
                return items
        except urllib.error.HTTPError as e:
            if e.code == 422:
                continue
            print(f"[news] Marketaux HTTP {e.code} for {ticker}")
        except Exception as e:
            print(f"[news] Marketaux error: {e}")
    return []


# ── Source 2: Anthropic web_search ──────────────────────────────────────────
def _anthropic_news(ticker: str, company_name: str, api_key: str) -> list[dict]:
    if not api_key:
        return []
    ctx = ssl.create_default_context()
    try:
        payload = json.dumps({
            "model": _HAIKU,
            "max_tokens": 1500,
            "tools": [{"type":"web_search_20250305","name":"web_search"}],
            "system": (
                "You are a financial news aggregator. Search the web for company news "
                "and return ONLY a JSON array. No markdown, no explanation. "
                "Each item must have: title, source, url, published (ISO8601 string), "
                "summary (1-2 sentences), type (news|announcement|result)."
            ),
            "messages": [{"role":"user","content":(
                f"Find the 10 most recent news articles about {company_name} "
                f"(NSE: {ticker}) from the past 30 days. Include earnings, analyst "
                f"ratings, business updates, management commentary. "
                f"Return ONLY a valid JSON array, nothing else."
            )}],
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={"Content-Type":"application/json",
                     "x-api-key":api_key,
                     "anthropic-version":"2023-06-01"},
            method="POST",
        )
        with urllib.request.urlopen(req, context=ctx, timeout=35) as r:
            resp = json.loads(r.read())

        text = "".join(b.get("text","") for b in resp.get("content",[]) if b.get("type")=="text").strip()
        # Strip markdown fences
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip().startswith("```") else lines[1:])
        s, e2 = text.find("["), text.rfind("]")+1
        if s == -1 or e2 == 0:
            return []
        articles = json.loads(text[s:e2])
        items = []
        for a in articles:
            pub = a.get("published","")
            try:
                dt  = datetime.fromisoformat(pub.replace("Z","+00:00"))
                ts  = dt.timestamp()
                pub = dt.strftime("%Y-%m-%dT%H:%M:%S")
            except Exception:
                ts = 0
            items.append({"title":a.get("title",""),"source":a.get("source","Web"),
                           "url":a.get("url",""),"published":pub,
                           "summary":a.get("summary",""),
                           "type":a.get("type","news"),"_ts":ts})
        return items
    except Exception as e:
        print(f"[news] Anthropic web_search error for {ticker}: {e}")
        return []


# ── Source 3: yfinance fallback ──────────────────────────────────────────────
def _yfinance_news(ticker: str) -> list[dict]:
    try:
        import yfinance as yf
        for sym in [ticker+".NS", ticker+".BO", ticker]:
            raw = yf.Ticker(sym).news or []
            if not raw:
                continue
            items = []
            for n in raw[:15]:
                ts = n.get("providerPublishTime") or 0
                try:
                    pub = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
                except Exception:
                    pub = None
                items.append({"title":n.get("title",""),"source":n.get("publisher",""),
                               "url":n.get("link",""),"published":pub,
                               "summary":n.get("summary",""),"type":"news","_ts":ts})
            if items:
                return items
    except Exception:
        pass
    return []


# ── Merge + dedupe ───────────────────────────────────────────────────────────
def _merge(lists: list[list[dict]]) -> list[dict]:
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
        except: return 0
    merged.sort(key=sk, reverse=True)
    for item in merged:
        item.pop("_ts", None)
    return merged[:25]


# ── Route ────────────────────────────────────────────────────────────────────
@router.get("/{ticker}/news")
def company_news(ticker: str, db: Session = Depends(get_db)):
    ticker = ticker.upper()
    cached = _NEWS_CACHE.get(ticker)
    if cached and time.time() - cached[0] < _CACHE_TTL:
        return cached[1]

    co = db.query(models.Company).filter_by(ticker=ticker).first()
    if not co:
        raise HTTPException(404, f"Unknown ticker {ticker}")

    mx_key  = os.getenv("MARKETAUX_API_KEY","")
    ant_key = os.getenv("ANTHROPIC_API_KEY","")

    sources, all_items = [], []

    mx = _marketaux_news(ticker, mx_key)
    if mx: all_items.append(mx); sources.append("marketaux")

    aw = _anthropic_news(ticker, co.name, ant_key)
    if aw: all_items.append(aw); sources.append("web_search")

    yf = _yfinance_news(ticker)
    if yf: all_items.append(yf); sources.append("yfinance")

    merged = _merge(all_items)
    result = {"ticker":ticker,"name":co.name,"count":len(merged),
              "sources":sources,"cached":False,"items":merged}
    _NEWS_CACHE[ticker] = (time.time(), {**result,"cached":True})
    return result
