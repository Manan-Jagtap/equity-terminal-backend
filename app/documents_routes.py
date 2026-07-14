"""
app/documents_routes.py — company documents (concalls, annual reports,
credit ratings, announcements) from the stored insight blob.

  GET /api/companies/{ticker}/documents → the insight's normalised "documents"
                                          dict, or {} — NEVER a 500. The data is
                                          populated by the ingester's _documents()
                                          best-effort call to IndianAPI /documents.
"""
import re
from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app import models

router = APIRouter(prefix="/api", tags=["documents"])

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
            return {}
        import copy
        return _normalize_docs(copy.deepcopy(docs))
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return {}


@router.get("/companies/{ticker}/transcript-summary")
def transcript_summary(ticker: str, refresh: bool = False, db: Session = Depends(get_db)):
    """AI summary of the latest earnings-call transcript — guidance, tone, risks,
    and a quarter-over-quarter language shift. Fetches the transcript PDF and
    grounds the summary strictly in it (returns available=False, never a made-up
    summary, when no transcript text can be extracted). Cached 6h."""
    co = db.query(models.Company).filter_by(ticker=ticker.upper()).first()
    if not co:
        return {"ticker": ticker.upper(), "available": False, "message": "Unknown ticker."}
    ins = db.query(models.CompanyInsight).filter_by(company_id=co.id).first()
    docs = (ins.data or {}).get("documents") if (ins and ins.data) else {}
    concalls = (docs or {}).get("concalls") or []
    from app.transcript_nlp import summarize_transcript
    return summarize_transcript(co.name, co.ticker, concalls, force_refresh=refresh)


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
