"""
app/bse_routes.py — FastAPI router for BSE document operations.

Endpoints:
    GET  /api/bse/announcements/{ticker}?quarter=Q4FY26[&raw=true]
         List BSE announcements for a quarter, with classifications.
         Does NOT store anything.

    POST /api/bse/fetch/{ticker}?quarter=Q4FY26[&force=false]
         Fetch + store IP + TRANSCRIPT for one ticker × quarter.
         Returns FetchResult JSON.

These are intentionally simple wrappers around the fetcher so we can
smoke-test from curl/the frontend before wiring up the one-pager flow.
"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.bse.fetcher import fetch_quarterly_documents, quarter_window
from app.bse.client import fetch_announcements
from app.bse.classifier import classify_announcement
from app.bse.scrip_codes import get_scrip_code
from app.admin_routes import require_admin
from app import models

router = APIRouter(prefix="/api/bse", tags=["bse"])


@router.get("/announcements/{ticker}")
def list_announcements(
    ticker: str,
    quarter: str = Query(..., description="e.g. Q4FY26"),
    raw: bool = Query(False, description="Include full raw announcement payload"),
):
    """List BSE announcements for ticker × quarter (read-only)."""
    ticker = ticker.upper()
    quarter = quarter.upper()
    scrip = get_scrip_code(ticker)
    if not scrip:
        raise HTTPException(404, f"no BSE scrip code for {ticker}")

    from_d, to_d = quarter_window(quarter)
    try:
        anns = fetch_announcements(scrip, from_d, to_d)
    except Exception as e:
        raise HTTPException(502, f"BSE API error: {e}")

    out = []
    for ann in anns:
        headline = ann.get("HEADLINE") or ann.get("NEWSSUB") or ""
        subcat = ann.get("SUBCATNAME") or ""
        cls = classify_announcement(headline, subcat)
        item = {
            "doc_type": cls.doc_type.value,
            "matched_on": cls.matched_on,
            "headline": headline,
            "subcategory": subcat,
            "dt_tm": ann.get("DT_TM"),
            "attachment": ann.get("ATTACHMENTNAME"),
            "news_id": ann.get("NEWSID"),
        }
        if raw:
            item["raw"] = ann
        out.append(item)

    return {
        "ticker": ticker,
        "quarter": quarter,
        "scrip_code": scrip,
        "window": {"from": from_d.isoformat(), "to": to_d.isoformat()},
        "count": len(out),
        "announcements": out,
    }


@router.post("/fetch/{ticker}")
def fetch(
    ticker: str,
    quarter: str = Query(..., description="e.g. Q4FY26"),
    force: bool = Query(False, description="Re-download even if cached in R2"),
    db: Session = Depends(get_db),
    _admin: models.User = Depends(require_admin),
):
    """Fetch + store IP + TRANSCRIPT for one ticker × quarter. ADMIN-ONLY: it
    triggers outbound BSE fetches, PDF downloads, R2 uploads and DB writes, so it
    must not be anonymously abusable for cost/DoS (audit D4)."""
    result = fetch_quarterly_documents(db, ticker.upper(), quarter.upper(), force=force)
    return {
        "ticker": result.ticker,
        "quarter": result.quarter,
        "scrip_code": result.scrip_code,
        "announcements_seen": result.announcements_seen,
        "documents_stored": result.documents_stored,
        "errors": result.errors,
        "skipped": result.skipped,
    }
