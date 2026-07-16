"""
app/transcript_nlp.py — earnings-call transcript fetch + text extraction.

The concall documents ingested from IndianAPI are LINKS, not text — so this
fetches the transcript PDF at request time and extracts its text (pdfplumber),
which the rule-based extractor in transcript_ingester turns into tone + key
points. 100% AI-free: the old LLM narrative summarizer was retired, so there is
no Anthropic call and no paid API here anymore.

No-fabrication rule: if no transcript text can be fetched/extracted, callers get
None and degrade to 'unavailable' — nothing is ever invented.
"""
from __future__ import annotations
import re
import io

import httpx

MAX_CHARS = 32000          # cap the extracted text handed to the rule extractor
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
            # BSE's AnnPdfOpen.aspx only serves the PDF WITHIN a browser session —
            # prime cookies by hitting the site root first (best-effort, short).
            if "bseindia.com" in url.lower():
                try:
                    client.get("https://www.bseindia.com/", timeout=httpx.Timeout(8.0))
                except Exception:
                    pass
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
