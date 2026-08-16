"""
app/documents_routes.py — company documents (concalls, annual reports,
credit ratings, announcements) from the stored insight blob.

  GET /api/companies/{ticker}/documents → the insight's normalised "documents"
                                          dict, or {} — NEVER a 500.

Provenance, stated honestly (as of 16 Aug 2026): the blob WAS populated by the
ingester's _documents() call to IndianAPI /documents until the vendor revoked
that endpoint on 24 Jul 2026 (404 "Endpoint not allowed" — DATA-12). The rows
served today are last-good values restored from the 22–23 Jul R2 backups and
kept alive by the DATA-12 merge-not-replace: the ingester still asks, gets
None, and keeps the stored copy. BSE's own announcements API has been anti-bot
blocked since mid-Jul 2026 (see _LIVE_BSE_OVERLAY below). So there is currently
NO live refresh path for this route — it serves stored filings that only get
older — and neither source may be described as live without re-checking it
first. If real-time filings become a requirement, the path is a licensed feed:
not scraping BSE, and no longer the vendor endpoint.
"""
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app import models

router = APIRouter(prefix="/api", tags=["documents"])

# --- Live BSE announcements layer (DORMANT — the source is closed) -----------
# Original intent: the stored `documents` blob only refreshed when the
# IndianAPI-quota-bound ingester next touched a company, so a fresh filing
# (results, investor deck, transcript) could take days to appear; BSE's
# quota-free announcements API was overlaid on top so the Docs tab reflected a
# result the moment it hit the exchange.
#
# What is actually true today: BSE's AnnGetData endpoint answers the JSON string
# "No Record Found!" to every scrip/date/param variant, from datacenter AND
# residential IPs (first seen 15 Jul 2026, endpoint-scoped anti-bot; re-confirmed
# 16 Aug 2026), and the scrip-search fallback in app/bse/scrip_codes.py 302s to
# error_Bse.html. The overlay has therefore not contributed a single row since
# mid-July — but it still COST every Docs-tab request a live round-trip to a dead
# host: for the ~920 tickers outside the hardcoded scrip table, get_scrip_code()
# ran a synchronous, uncached, 15 s-timeout HTTP call in the request thread (one
# WARNING log line per page view, and the frontend fetches /documents twice per
# Company page), and each per-scrip cache miss parked one of the two pool
# workers on a 30 s-timeout announcements call. On a bad day at the anti-bot
# layer that is a 15 s stall on the Docs tab for nothing.
#
# _LIVE_BSE_OVERLAY short-circuits _merge_live_bse. The overlay code below is
# kept intact (not deleted) so it can be switched back on after a re-check shows
# BSE answering again, or re-pointed at a licensed feed with the same row shape.
# Do NOT try to defeat the anti-bot layer to revive it.
_LIVE_BSE_OVERLAY = False
_BSE_TTL = 1800  # 30 min
_bse_cache: dict = {}          # scrip -> (ts, [items])
_bse_pool = ThreadPoolExecutor(max_workers=2)


def _bse_daykey(s) -> str | None:
    m = re.search(r"(20[0-3]\d)[-/ ]?(\d{2})[-/ ]?(\d{2})", str(s or ""))
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def _live_bse_items(scrip: str, days: int = 150) -> list:
    now = time.time()
    hit = _bse_cache.get(scrip)
    if hit and now - hit[0] < _BSE_TTL:
        return hit[1]
    items = []
    try:
        from app.bse.client import fetch_announcements, BSE_ATTACHMENT_BASE
        from app.bse.classifier import classify_announcement
        frm = date.today() - timedelta(days=days)
        # Single attempt — the 30-min cache and the caller's wait-bound make a
        # retry storm both unnecessary and harmful to page latency.
        anns = fetch_announcements(scrip, frm, date.today(), max_retries=1)
        for a in anns:
            headline = (a.get("HEADLINE") or a.get("NEWSSUB") or "").strip()
            subcat = (a.get("SUBCATNAME") or "").strip()
            att = (a.get("ATTACHMENTNAME") or "").strip()
            if not headline and not subcat:
                continue
            url = f"{BSE_ATTACHMENT_BASE}/{att}" if att else (a.get("NSURL") or None)
            cls = classify_announcement(headline, subcat)
            items.append({
                "headline": headline or subcat, "subcat": subcat,
                "dt": (a.get("DT_TM") or "").strip(), "url": url,
                "doc_type": cls.doc_type.value,
            })
    except Exception:
        # Serve the last good payload on failure; never raise into the page.
        items = hit[1] if hit else []
    _bse_cache[scrip] = (now, items)
    return items


def _merge_live_bse(ticker: str, docs: dict) -> dict:
    if not _LIVE_BSE_OVERLAY:
        # Dead source (see _LIVE_BSE_OVERLAY above): don't dial BSE on every
        # request just to hear "No Record Found!" again — serve the stored blob.
        return docs
    try:
        from app.bse.scrip_codes import get_scrip_code
        scrip = get_scrip_code(ticker.upper())
        if not scrip:
            return docs
        # Warm cache returns instantly; otherwise bound the wait so a slow BSE
        # call never blocks the docs page (the thread still fills the cache).
        try:
            live = _bse_pool.submit(_live_bse_items, scrip).result(timeout=6)
        except Exception:
            return docs
        if not live:
            return docs

        anns = list(docs.get("announcements") or [])
        concalls = list(docs.get("concalls") or [])
        seen_ann = {((a.get("title") or "")[:40].lower(), _bse_daykey(a.get("date")))
                    for a in anns if isinstance(a, dict)}
        seen_url = {a.get("url") for a in anns if isinstance(a, dict) and a.get("url")}
        concall_by_day = {}
        for c in concalls:
            if isinstance(c, dict):
                dk = _bse_daykey(c.get("date"))
                if dk:
                    concall_by_day[dk] = c

        new_anns = []
        for it in live:
            dk = _bse_daykey(it["dt"])
            title = it["headline"]
            # Investor decks / transcripts belong in the Concalls card…
            if it["doc_type"] in ("IP", "TRANSCRIPT") and dk and it["url"]:
                row = concall_by_day.get(dk)
                if row is None:
                    row = {"date": dk}
                    concall_by_day[dk] = row
                    concalls.append(row)
                if it["doc_type"] == "IP" and not row.get("ppt"):
                    row["ppt"] = it["url"]
                if it["doc_type"] == "TRANSCRIPT" and not row.get("transcript"):
                    row["transcript"] = it["url"]
                continue
            # …everything else (results, press releases, board outcomes) lists
            # under Announcements, newest first, deduped against the blob.
            key = (title[:40].lower(), dk)
            if key in seen_ann or (it["url"] and it["url"] in seen_url):
                continue
            seen_ann.add(key)
            new_anns.append({"title": title, "date": (dk or it["dt"] or ""), "url": it["url"]})

        new_anns.sort(key=lambda a: a.get("date") or "", reverse=True)
        docs["announcements"] = (new_anns + anns)[:40]
        docs["concalls"] = concalls
    except Exception:
        pass                                  # freshness overlay is best-effort
    return docs

_YEAR_RX = re.compile(r"(20[0-3]\d)")
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_RECORDING_RX = re.compile(
    r"audio\s+recording|recording\s+of\s+.*(earnings|concall|conference|analyst)"
    r"|audio\s+link|call\s+recording", re.I)


def _month_no(token: str):
    return _MONTHS.get((token or "")[:3].lower())


def _fix_rating_years(ratings: list) -> None:
    """The vendor omits the year on CURRENT-year rating rows ("30 Jun"), which
    the browser's Date parser silently turns into 2001. The rating agency URL
    (CRISIL/CARE filenames) embeds the real date — recover the year from it,
    accepting only a candidate that isn't in the future."""
    today = date.today()
    for r in ratings:
        if not isinstance(r, dict):
            continue
        d = (r.get("date") or "").strip()
        if not d or re.search(r"\d{4}", d):
            continue                      # already carries a year
        m = re.match(r"^(\d{1,2})\s+([A-Za-z]{3,9})$", d)
        mon = _month_no(m.group(2)) if m else None
        if not (m and mon):
            continue
        for y in sorted({int(x) for x in _YEAR_RX.findall(r.get("url") or "")}, reverse=True):
            try:
                cand = date(y, mon, int(m.group(1)))
            except ValueError:
                continue
            if cand <= today + timedelta(days=45):
                r["date"] = f"{d} {y}"
                break


def _attach_recordings(docs: dict) -> None:
    """BSE announcements carry the earnings-call audio recordings the concall
    feed lacks. Attach each to the concall of the same month; a recording newer
    than every listed concall becomes its own row (the transcript usually lands
    a few days later)."""
    anns = docs.get("announcements") or []
    ccs = docs.get("concalls")
    if not isinstance(ccs, list):
        ccs = docs["concalls"] = []
    for a in anns:
        if not isinstance(a, dict) or not _RECORDING_RX.search(a.get("title") or ""):
            continue
        url = a.get("url")
        if not url:
            continue
        token = (a.get("date") or "").split(" - ")[0].strip()
        mon = None
        m = re.match(r"^(?:\d{1,2}\s+)?([A-Za-z]{3,9})", token)
        if m:
            mon = _month_no(m.group(1))
        placed = False
        for c in ccs:
            cm = re.match(r"^([A-Za-z]{3,9})\s+\d{4}$", (c.get("date") or "").strip())
            if cm and mon and _month_no(cm.group(1)) == mon and not c.get("recording"):
                c["recording"] = url
                placed = True
                break
        if not placed and not any(c.get("recording") == url for c in ccs):
            ccs.insert(0, {"date": token or None, "recording": url,
                           "transcript": None, "ppt": None, "summary": None})


def _dedupe_by_url(rows: list) -> list:
    seen, out = set(), []
    for r in rows:
        u = r.get("url") if isinstance(r, dict) else None
        if u and u in seen:
            continue
        if u:
            seen.add(u)
        out.append(r)
    return out


def _normalize_docs(docs: dict) -> dict:
    try:
        if isinstance(docs.get("credit_ratings"), list):
            docs["credit_ratings"] = _dedupe_by_url(docs["credit_ratings"])
            _fix_rating_years(docs["credit_ratings"])
        _attach_recordings(docs)
    except Exception:
        pass                              # serving must never 500 over polish
    return docs


@router.get("/companies/{ticker}/documents")
def company_documents(ticker: str, db: Session = Depends(get_db)):
    try:
        co = db.query(models.Company).filter_by(ticker=ticker.upper()).first()
        if not co:
            return {}
        ins = db.query(models.CompanyInsight).filter_by(company_id=co.id).first()
        docs = (ins.data or {}).get("documents") if ins else None
        if not isinstance(docs, dict):
            docs = {}
        import copy
        docs = _merge_live_bse(co.ticker, copy.deepcopy(docs))
        return _normalize_docs(docs)
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return {}


@router.get("/companies/{ticker}/transcript-summary")
def transcript_summary(ticker: str, refresh: bool = False, db: Session = Depends(get_db)):
    """RULES-BASED summary of the latest earnings call (NO LLM, zero API cost).
    A readable research-note narrative assembled deterministically from the same
    extractors that feed the concall-tone signal — management tone, guidance,
    margins, capex, demand, risks + the numeric KPIs — plus a quarter-over-quarter
    tone shift. Returns available=False (never a fabricated summary) when no
    transcript text can be fetched/extracted. Cached 6h."""
    co = db.query(models.Company).filter_by(ticker=ticker.upper()).first()
    if not co:
        return {"ticker": ticker.upper(), "available": False, "message": "Unknown ticker."}
    from app.transcript_ingester import summarize_transcript
    return summarize_transcript(db, co.name, co.ticker, force_refresh=refresh)


@router.get("/companies/{ticker}/scorecard")
def company_scorecard(ticker: str, db: Session = Depends(get_db)):
    """Per-stock scorecard: five sub-scores (valuation, quality, growth,
    momentum, safety) + an overall grade + auto red/green flags — a synthesis
    of the Fund Manager v4 evidence for this name. Returns available=False when
    the nightly evidence build hasn't covered it."""
    from app.manager_engine import load_evidence
    from app.scorecard import build_scorecard
    ev = ((load_evidence(db) or {}).get("names") or {}).get(ticker.upper())
    sc = build_scorecard(ev or {})
    sc["ticker"] = ticker.upper()
    return sc


@router.get("/manager/hidden-gems")
def manager_hidden_gems(limit: int = 12, refresh: bool = False, db: Session = Depends(get_db)):
    """The Fund Manager's hidden-gems / multibagger finder: under-followed
    small/mid-cap quality compounders (clean forensics, durable ROE, a real
    growth engine, a fair price) surfaced from the full universe, each with an
    honest thesis and the risks stated. Potential, never a promise. Cached to
    the nightly evidence build."""
    from app.hidden_gems import find_hidden_gems
    return find_hidden_gems(db, limit=limit, refresh=refresh)


@router.get("/companies/{ticker}/transcript-insight")
def transcript_insight(ticker: str, db: Session = Depends(get_db)):
    """Pre-extracted key points from the latest earnings-call transcript —
    guidance, margins, capex, demand, risks, and a management-tone score.
    Populated proactively by the daily transcript ingester (free, rule-based);
    returns available=False when no transcript has been processed yet."""
    from app import transcript_ingester
    data = transcript_ingester.load(db, ticker)
    if not data:
        return {"ticker": ticker.upper(), "available": False,
                "message": "No processed transcript yet — the ingester runs daily."}
    return {"available": True, **data}
