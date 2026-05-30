"""
scripts/test_bse_fetch.py — Smoke-test the BSE→R2 pipeline for one ticker.

Usage:
    python -m scripts.test_bse_fetch MUTHOOTFIN Q4FY26
    python -m scripts.test_bse_fetch MUTHOOTFIN Q4FY26 --raw       # show raw BSE response
    python -m scripts.test_bse_fetch MUTHOOTFIN Q4FY26 --force     # re-download even if cached

Prints:
    - Resolved scrip code
    - Number of announcements found in window
    - Classification of each announcement
    - Which docs were stored, where, file sizes
    - Errors / skips
"""
from __future__ import annotations
import argparse
import json
import sys
from dataclasses import asdict

from app.database import SessionLocal
from app.bse.fetcher import fetch_quarterly_documents, quarter_window
from app.bse.client import fetch_announcements
from app.bse.classifier import classify_announcement
from app.bse.scrip_codes import get_scrip_code


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("quarter")
    ap.add_argument("--raw", action="store_true",
                    help="Show raw BSE announcements without storing anything")
    ap.add_argument("--force", action="store_true",
                    help="Re-download even if already in R2")
    args = ap.parse_args()

    ticker = args.ticker.upper()
    quarter = args.quarter.upper()

    if args.raw:
        scrip = get_scrip_code(ticker)
        if not scrip:
            print(f"✗ no scrip code for {ticker}")
            sys.exit(1)
        from_d, to_d = quarter_window(quarter)
        print(f"Ticker: {ticker} | scrip: {scrip} | window: {from_d} → {to_d}")
        print()
        anns = fetch_announcements(scrip, from_d, to_d)
        print(f"Got {len(anns)} announcement(s):\n")
        for i, ann in enumerate(anns, 1):
            headline = ann.get("HEADLINE") or ann.get("NEWSSUB") or ""
            subcat = ann.get("SUBCATNAME") or ""
            cls = classify_announcement(headline, subcat)
            print(f"  [{i}] {cls.doc_type.value:14} (via {cls.matched_on})")
            print(f"       HEADLINE: {headline[:100]}")
            if subcat:
                print(f"       SUBCAT:   {subcat}")
            print(f"       DT_TM:    {ann.get('DT_TM') or '-'}")
            print(f"       ATTACH:   {ann.get('ATTACHMENTNAME') or '-'}")
            print()
        return

    db = SessionLocal()
    try:
        result = fetch_quarterly_documents(db, ticker, quarter, force=args.force)
        print(json.dumps({
            "ticker": result.ticker,
            "quarter": result.quarter,
            "scrip_code": result.scrip_code,
            "announcements_seen": result.announcements_seen,
            "documents_stored": result.documents_stored,
            "errors": result.errors,
            "skipped": result.skipped,
        }, indent=2, default=str))
    finally:
        db.close()


if __name__ == "__main__":
    main()
