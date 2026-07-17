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
import ipaddress
import socket
from urllib.parse import urlparse

import httpx

MAX_CHARS = 32000          # cap the extracted text handed to the rule extractor
MAX_BYTES = 25 * 1024 * 1024   # skip absurdly large downloads
MAX_PAGES = 60             # hard page cap so pdfplumber can't run away
MIN_TEXT = 500             # below this we treat extraction as failed


def _is_public_host(host: str) -> bool:
    """True only if EVERY resolved IP for `host` is a routable public address.
    Blocks SSRF to loopback / RFC-1918 / link-local (169.254.169.254 metadata) /
    ULA / reserved ranges — the transcript URL comes from stored/vendor data and
    could point (or redirect) at internal services (audit D3)."""
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    for info in infos:
        ip = info[4][0]
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast or addr.is_unspecified):
            return False
    return bool(infos)


def _safe_url(url: str) -> bool:
    """http/https scheme + a public, resolvable host."""
    try:
        p = urlparse(url)
    except Exception:
        return False
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    return _is_public_host(p.hostname)

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
    if not url or not _safe_url(str(url)):
        return None
    try:
        # follow_redirects=False + manual, SSRF-revalidated redirect handling:
        # an allowed public URL could 30x to an internal host, so every hop's
        # target must pass _safe_url before we fetch it.
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=False, headers=_HEADERS) as client:
            # BSE's AnnPdfOpen.aspx only serves the PDF WITHIN a browser session —
            # prime cookies by hitting the site root first (best-effort, short).
            if "bseindia.com" in url.lower():
                try:
                    client.get("https://www.bseindia.com/", timeout=httpx.Timeout(8.0))
                except Exception:
                    pass
            cur = str(url)
            r = None
            for _hop in range(5):
                r = client.get(cur)
                if r.is_redirect and r.headers.get("location"):
                    nxt = str(r.next_request.url) if r.next_request else r.headers["location"]
                    if not _safe_url(nxt):
                        return None
                    cur = nxt
                    continue
                break
            if r is None:
                return None
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
