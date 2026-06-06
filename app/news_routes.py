"""
app/news_routes.py — Company news endpoint.

Sources (merged, deduplicated, sorted newest-first, cached 30 min):
  1. IndianAPI /company_news  (INDIANAPI_KEY env var)  ← primary
  2. Marketaux API            (MARKETAUX_API_KEY env var)
  3. Anthropic web_search     (ANTHROPIC_API_KEY env var, opt-in)

No Yahoo Finance — fully removed.

GET /api/companies/{ticker}/news
"""
from __future__ import annotations
import os, json, time, ssl, urllib.request
from datetime import datetime, timezone

import requests
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app import models

router = APIRouter(prefix="/api/companies")

_NEWS_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL  = 1800
_HAIKU      = "claude-haiku-4-5-20251001"
_INDIANAPI_BASE = os.getenv("INDIANAPI_BASE", "https://dev.indianapi.in").rstrip("/")


# ── Source 1: Marketaux ──────────────────────────────────────────────────────
def _marketaux_news(ticker: str, api_key: str) -> tuple[list[dict], str | None]:
    if not api_key:
        return [], "no_key"
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
                return items, None
        except urllib.error.HTTPError as e:
            if e.code == 422:
                continue
            return [], f"http_{e.code}"
        except Exception as e:
            return [], str(e)[:80]
    return [], "no_results"


# ── Source 2: Anthropic web_search (multi-turn) ──────────────────────────────
def _anthropic_news(ticker: str, company_name: str, api_key: str) -> tuple[list[dict], str | None]:
    if os.getenv("NEWS_LLM_ENABLED", "false").lower() != "true":
        return [], "news_llm_paused"
    if not api_key:
        return [], "no_key"
    ctx = ssl.create_default_context()
    try:
        messages = [{"role":"user","content":(
            f"Search the web and find 8 recent news articles about "
            f"{company_name} (NSE: {ticker}) from the past 30 days. "
            f"Include earnings results, analyst ratings, business updates. "
            f"After searching, return ONLY a valid JSON array with fields: "
            f"title, source, url, published (YYYY-MM-DD), summary, type (news|announcement|result). "
            f"No markdown fences, no explanation text."
        )}]

        for _round in range(4):  # allow up to 4 tool-use rounds
            payload = json.dumps({
                "model": _HAIKU,
                "max_tokens": 2500,
                "tools": [{
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": 5,
                    "user_location": {"type":"approximate","country":"IN","timezone":"Asia/Kolkata"}
                }],
                "messages": messages,
            }).encode()

            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=payload,
                headers={"Content-Type":"application/json",
                         "x-api-key":api_key,
                         "anthropic-version":"2023-06-01"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, context=ctx, timeout=50) as r:
                    resp = json.loads(r.read())
            except urllib.error.HTTPError as e:
                body = e.read().decode()[:200]
                return [], f"http_{e.code}:{body}"

            stop    = resp.get("stop_reason","")
            content = resp.get("content", [])

            if stop == "tool_use":
                # Continue conversation with tool results
                messages.append({"role":"assistant","content":content})
                tool_results = []
                for block in content:
                    if block.get("type") == "tool_use":
                        tool_results.append({
                            "type":"tool_result",
                            "tool_use_id":block["id"],
                            "content":"Search completed successfully."
                        })
                if tool_results:
                    messages.append({"role":"user","content":tool_results})
                continue

            # stop_reason == "end_turn" — extract text
            text = "".join(b.get("text","") for b in content if b.get("type")=="text").strip()
            if not text:
                return [], "empty_response"

            # Strip markdown fences
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(l for l in lines if not l.strip().startswith("```"))

            s = text.find("[")
            e2 = text.rfind("]") + 1
            if s == -1 or e2 == 0:
                # Try to extract any JSON-like content
                return [], f"no_json_array:{text[:100]}"

            try:
                articles = json.loads(text[s:e2])
            except json.JSONDecodeError as je:
                return [], f"json_error:{str(je)[:80]}"

            items = []
            for a in articles:
                pub = str(a.get("published",""))
                try:
                    # Handle both full ISO and date-only formats
                    if "T" in pub:
                        dt = datetime.fromisoformat(pub.replace("Z","+00:00"))
                    else:
                        dt = datetime.strptime(pub[:10], "%Y-%m-%d")
                    ts  = dt.timestamp()
                    pub = dt.strftime("%Y-%m-%dT%H:%M:%S")
                except Exception:
                    ts = 0
                items.append({
                    "title":   str(a.get("title","")),
                    "source":  str(a.get("source","Web")),
                    "url":     str(a.get("url","")),
                    "published": pub,
                    "summary": str(a.get("summary","")),
                    "type":    str(a.get("type","news")),
                    "_ts":     ts,
                })
            return items, None

        return [], "max_rounds_exceeded"

    except Exception as e:
        return [], f"exception:{str(e)[:100]}"


# ── Source 1 (primary): IndianAPI /company_news ──────────────────────────────
def _indianapi_news(ticker: str) -> tuple[list[dict], str | None]:
    key = os.getenv("INDIANAPI_KEY", "").strip()
    if not key:
        return [], "no_key"
    try:
        r = requests.get(_INDIANAPI_BASE + "/company_news",
                         headers={"X-API-Key": key, "x-api-key": key},
                         params={"stock_name": ticker}, timeout=20)
        if r.status_code != 200:
            return [], f"http_{r.status_code}"
        data = r.json()
    except Exception as e:
        return [], str(e)[:80]

    arts = data if isinstance(data, list) else (data.get("news") or data.get("data") or [])
    items = []
    for a in arts[:25]:
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
            "title":   a.get("title", ""),
            "source":  a.get("source", "IndianAPI"),
            "url":     a.get("article_link") or a.get("url", ""),
            "published": pub,
            "summary": a.get("summary", ""),
            "type":    "news",
            "_ts":     ts,
        })
    return (items, None) if items else ([], "no_results")


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
        except: return 0
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

    co = db.query(models.Company).filter_by(ticker=ticker).first()
    if not co:
        raise HTTPException(404, f"Unknown ticker {ticker}")

    mx_key  = os.getenv("MARKETAUX_API_KEY","")
    ant_key = os.getenv("ANTHROPIC_API_KEY","")

    sources, all_items, debug = [], [], {}

    # Primary: IndianAPI company news.
    ia_items, ia_err = _indianapi_news(ticker)
    if ia_items: all_items.append(ia_items); sources.append("indianapi")
    if ia_err:   debug["indianapi_err"] = ia_err

    mx_items, mx_err = _marketaux_news(ticker, mx_key)
    if mx_items: all_items.append(mx_items); sources.append("marketaux")
    if mx_err:   debug["marketaux_err"] = mx_err

    aw_items, aw_err = _anthropic_news(ticker, co.name, ant_key)
    if aw_items: all_items.append(aw_items); sources.append("web_search")
    if aw_err:   debug["web_search_err"] = aw_err

    merged = _merge(all_items)
    result = {"ticker":ticker,"name":co.name,"count":len(merged),
              "sources":sources,"cached":False,"items":merged,"debug":debug}
    _NEWS_CACHE[ticker] = (time.time(), {**result,"cached":True})
    return result
