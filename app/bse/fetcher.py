"""
app/bse/fetcher.py — End-to-end orchestrator.

Given a ticker and quarter, this module:
    1. Resolves the BSE scrip code
    2. Computes the date window for that quarter
    3. Calls BSE API to list announcements in window
    4. Classifies each announcement
    5. Downloads the best IP + TRANSCRIPT PDFs
    6. Uploads PDFs to R2
    7. Writes rows to quarterly_documents table

Idempotent: if a (company, quarter, doc_type) row already exists with a
non-empty r2_key, skips that doc.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app import models
from app.r2 import upload_bytes, build_doc_key, object_exists
from .client import fetch_announcements, download_attachment, parse_filing_date
from .classifier import classify_announcement, DocType
from .scrip_codes import get_scrip_code

log = logging.getLogger(__name__)


# Quarter end dates (Indian financial year: Apr 1 – Mar 31)
# Q1 = Apr–Jun, Q2 = Jul–Sep, Q3 = Oct–Dec, Q4 = Jan–Mar
def quarter_window(quarter: str) -> tuple[date, date]:
    """
    Parse a quarter string like 'Q4FY26' and return the fetch window:
    quarter end → 90 days after (covers filing lag).

    FY26 = April 2025 – March 2026.
    Q4FY26 = Jan–Mar 2026 → window: 2026-03-31 → 2026-06-29.
    """
    q = quarter.upper().strip()
    if not (q.startswith("Q") and "FY" in q):
        raise ValueError(f"bad quarter format: {quarter!r} (want like 'Q4FY26')")
    qnum = int(q[1])
    fy = int(q.split("FY")[1])
    # FY26 ends March 2026 → calendar year 2026 for Q4, 2025 for Q1-Q3
    if qnum == 1:
        end_month, end_day = 6, 30
        end_year = 2000 + fy - 1  # FY26 Q1 = Jun 2025
    elif qnum == 2:
        end_month, end_day = 9, 30
        end_year = 2000 + fy - 1
    elif qnum == 3:
        end_month, end_day = 12, 31
        end_year = 2000 + fy - 1
    elif qnum == 4:
        end_month, end_day = 3, 31
        end_year = 2000 + fy
    else:
        raise ValueError(f"bad quarter number: {qnum}")

    q_end = date(end_year, end_month, end_day)
    return q_end, q_end + timedelta(days=90)


@dataclass
class FetchResult:
    ticker: str
    quarter: str
    scrip_code: Optional[str] = None
    announcements_seen: int = 0
    documents_stored: dict[str, str] = field(default_factory=dict)  # doc_type → r2_key
    errors: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def fetch_quarterly_documents(
    db: Session,
    ticker: str,
    quarter: str,
    *,
    force: bool = False,
    doc_types: tuple[DocType, ...] = (DocType.IP, DocType.TRANSCRIPT),
) -> FetchResult:
    """
    Fetch IP + transcript for one ticker × quarter, upload to R2, write DB rows.

    Args:
        db: SQLAlchemy session
        ticker: NSE ticker (e.g. 'MUTHOOTFIN')
        quarter: e.g. 'Q4FY26'
        force: re-download even if R2 object + DB row already exist
        doc_types: which doc types to fetch

    Returns:
        FetchResult summarizing what happened.
    """
    result = FetchResult(ticker=ticker.upper(), quarter=quarter.upper())

    co = db.query(models.Company).filter(models.Company.ticker == result.ticker).first()
    if not co:
        result.errors.append(f"company not in DB: {result.ticker}")
        return result

    scrip = get_scrip_code(result.ticker)
    if not scrip:
        result.errors.append(f"no BSE scrip code found for {result.ticker}")
        return result
    result.scrip_code = scrip

    # Persist scrip code on the company row for next time
    if co.bse_scrip_code != scrip:
        co.bse_scrip_code = scrip
        db.commit()

    from_date, to_date = quarter_window(result.quarter)

    try:
        announcements = fetch_announcements(scrip, from_date, to_date)
    except Exception as e:
        result.errors.append(f"BSE API failed: {e}")
        return result
    result.announcements_seen = len(announcements)

    # Pick best announcement per doc_type
    picks: dict[DocType, dict] = {}
    for ann in announcements:
        headline = ann.get("HEADLINE") or ann.get("NEWSSUB") or ""
        subcat = ann.get("SUBCATNAME") or ann.get("NEWS_SUB") or ""
        cls = classify_announcement(headline, subcat)
        if cls.doc_type in doc_types and cls.doc_type not in picks:
            # First match wins; BSE returns newest first by default
            picks[cls.doc_type] = ann

    for doc_type in doc_types:
        if doc_type not in picks:
            result.skipped.append(f"{doc_type.value}: no matching announcement")
            continue

        ann = picks[doc_type]
        attachment = ann.get("ATTACHMENTNAME") or ann.get("AttachmentName")
        if not attachment:
            result.errors.append(f"{doc_type.value}: no attachment name in {ann}")
            continue

        r2_key = build_doc_key(result.ticker, result.quarter, doc_type.value)

        # Idempotency check
        if not force:
            existing = (db.query(models.QuarterlyDocument)
                          .filter_by(company_id=co.id,
                                     quarter=result.quarter,
                                     doc_type=doc_type.value)
                          .first())
            if existing and existing.r2_key and object_exists(existing.r2_key):
                log.info("%s %s %s already in R2, skipping",
                         result.ticker, result.quarter, doc_type.value)
                result.documents_stored[doc_type.value] = existing.r2_key
                continue

        # Download from BSE
        try:
            pdf_bytes = download_attachment(attachment)
        except Exception as e:
            result.errors.append(f"{doc_type.value}: download failed: {e}")
            continue

        # Upload to R2
        try:
            upload_bytes(r2_key, pdf_bytes)
        except Exception as e:
            result.errors.append(f"{doc_type.value}: R2 upload failed: {e}")
            continue

        # Write DB row
        filing_dt = parse_filing_date(ann.get("DT_TM") or ann.get("News_submission_dt") or "")
        bse_id = ann.get("NEWSID") or ann.get("NewsId")
        source_url = f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{attachment}"

        existing = (db.query(models.QuarterlyDocument)
                      .filter_by(company_id=co.id,
                                 quarter=result.quarter,
                                 doc_type=doc_type.value)
                      .first())
        if existing:
            existing.bse_announcement_id = str(bse_id) if bse_id else None
            existing.bse_filing_date = filing_dt
            existing.source_url = source_url
            existing.r2_key = r2_key
            existing.file_size_bytes = len(pdf_bytes)
        else:
            db.add(models.QuarterlyDocument(
                company_id=co.id,
                quarter=result.quarter,
                doc_type=doc_type.value,
                bse_announcement_id=str(bse_id) if bse_id else None,
                bse_filing_date=filing_dt,
                source_url=source_url,
                r2_key=r2_key,
                file_size_bytes=len(pdf_bytes),
            ))
        db.commit()

        result.documents_stored[doc_type.value] = r2_key
        log.info("stored %s %s %s → %s (%d bytes)",
                 result.ticker, result.quarter, doc_type.value, r2_key, len(pdf_bytes))

    return result
