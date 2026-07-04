"""
app/transcript_nlp.py — earnings-call transcript summarizer (strictly grounded).

The concall documents ingested from IndianAPI are LINKS, not text — so this
fetches the transcript PDF at request time, extracts its text (pdfplumber), and
asks Claude to summarize guidance / tone / risks GROUNDED ONLY in that text,
plus a quarter-over-quarter language shift when a prior transcript is available.

No-fabrication rule (same as the rest of the terminal): if no transcript text
can be fetched/extracted, it returns available=False with a clear reason — it
never invents a summary. Results cache 6h. Reuses the thesis Claude caller.
"""
from __future__ import annotations
import os
import io
import re
import hashlib

import httpx

from .thesis import call_claude, ThesisCache

_cache = ThesisCache(ttl_seconds=3600 * 6)
MAX_CHARS = 32000          # ~8k tokens fed to Claude — bounds cost + latency
MAX_BYTES = 25 * 1024 * 1024   # skip absurdly large downloads
MAX_PAGES = 60             # hard page cap so pdfplumber can't run away
MIN_TEXT = 500             # below this we treat extraction as failed

# Browser-like headers — BSE (AnnPdfOpen.aspx) STALLS header-less requests, which
# is what hung the first version. UA + Referer make it serve the PDF normally.
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/pdf,text/html,*/*",
    "Referer": "https://www.bseindia.com/",
}
# Hard, bounded timeout so a slow source can never hang the request thread.
_TIMEOUT = httpx.Timeout(connect=8.0, read=18.0, write=8.0, pool=8.0)


def fetch_transcript_text(url: str) -> str | None:
    """Download a transcript (PDF or HTML) and extract text, capped at MAX_CHARS /
    MAX_PAGES. Bounded by _TIMEOUT. Returns None on any failure or if too little
    text is recovered (so the caller degrades to 'unavailable', never noise)."""
    if not url or not str(url).lower().startswith("http"):
        return None
    try:
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=True, headers=_HEADERS) as client:
            r = client.get(url)
            r.raise_for_status()
            raw = r.content
        if not raw or len(raw) > MAX_BYTES:
            return None
        ctype = (r.headers.get("content-type") or "").lower()
        text = ""
        if "pdf" in ctype or str(url).lower().endswith(".pdf") or raw[:4] == b"%PDF":
            import pdfplumber
            with pdfplumber.open(io.BytesIO(raw)) as pdf:
                parts, n = [], 0
                for page in pdf.pages:
                    parts.append(page.extract_text() or "")
                    n += 1
                    if n >= MAX_PAGES or sum(len(p) for p in parts) > MAX_CHARS:
                        break
                text = "\n".join(parts)
        else:
            text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", r.text))   # crude HTML strip
        text = (text or "").strip()
        return text[:MAX_CHARS] if len(text) >= MIN_TEXT else None
    except Exception:
        return None


def build_prompt(company_name: str, ticker: str, quarter, text: str, prev_text: str | None = None):
    """(system, user) for the transcript summary — strict grounding + fixed headers."""
    system = (
        "You are an equity research analyst summarizing an earnings-call transcript. "
        "Use ONLY the transcript text provided — never add outside facts or invent figures. "
        "If the transcript does not cover a topic, say so plainly. Be concise, specific and "
        "neutral. Write in prose under the exact headers given. No disclaimers, no preamble."
    )
    diff_block = ("\n\nPRIOR-QUARTER TRANSCRIPT (for language/tone comparison only):\n" + prev_text[:12000]) if prev_text else ""
    qtxt = f" — {quarter}" if quarter else ""
    qoq = ("How the tone, confidence and priorities shifted versus the prior-quarter call above — "
           "cite the change in language." if prev_text else
           "Not enough transcript history for a quarter-over-quarter comparison this time.")
    user = f"""Summarize the earnings call for {company_name} ({ticker}){qtxt}.

TRANSCRIPT:
{text[:MAX_CHARS]}{diff_block}

Use these exact headers:

## Guidance & Outlook
Forward growth, margins, demand and any explicit targets management gave. Quote guidance only if it is in the transcript.

## Management Tone
Overall sentiment (confident / cautious / defensive) with the specific language that evidences it.

## Key Risks & Watch-items
Concerns, headwinds or soft spots management flagged or was pressed on.

## Quarter-over-quarter shift
{qoq}
"""
    return system, user


def summarize_transcript(company_name: str, ticker: str, concalls: list, api_key: str = "",
                         force_refresh: bool = False) -> dict:
    """concalls: [{date, transcript(url), ...}] (newest first). Fetches the latest
    transcript (+ the prior one for a diff), summarizes, and caches by transcript URL."""
    api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
    withlink = [c for c in (concalls or []) if c.get("transcript")]
    if not withlink:
        return {"ticker": ticker, "available": False,
                "message": "No concall transcript link is available for this company yet."}
    latest = withlink[0]
    key = hashlib.md5(f"{ticker}:{latest.get('transcript')}".encode()).hexdigest()[:12]
    if not force_refresh:
        cached = _cache.get(ticker, key)
        if cached:
            return {**cached, "cached": True}
    if not api_key:
        return {"ticker": ticker, "available": False,
                "message": "ANTHROPIC_API_KEY is not configured on the backend."}

    text = fetch_transcript_text(latest.get("transcript"))
    if not text:
        return {"ticker": ticker, "available": False, "quarter": latest.get("date"),
                "message": "The transcript link could not be fetched or held no extractable text."}
    # Single transcript per call for bounded latency (one fetch + one parse).
    # QoQ language diff is deferred to a background job (see note in the route).
    prev_text = None

    system, user = build_prompt(company_name, ticker, latest.get("date"), text, prev_text)
    try:
        summary = call_claude(system, user, api_key, max_tokens=1100)
    except Exception as e:
        return {"ticker": ticker, "available": False,
                "message": f"Summarization failed: {str(e)[:160]}"}

    result = {"ticker": ticker, "available": True, "quarter": latest.get("date"),
              "has_prior": bool(prev_text), "summary": summary, "cached": False}
    _cache.set(ticker, key, result)
    return result
