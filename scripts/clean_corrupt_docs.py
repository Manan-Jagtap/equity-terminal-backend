"""
scripts/clean_corrupt_docs.py — find and remove corrupt (non-PDF) documents.

The pre-validation downloader stored BSE error pages (~8KB HTML) as .pdf.
This finds every quarterly_documents row whose file_size_bytes is below the
PDF floor, deletes the R2 object, and deletes the DB row.

Usage:
    railway run python -m scripts.clean_corrupt_docs --dry-run
    railway run python -m scripts.clean_corrupt_docs
"""
from __future__ import annotations
import argparse

from app.database import SessionLocal
from app import models
from app.r2 import delete_object

MIN_PDF_BYTES = 20_000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--threshold", type=int, default=MIN_PDF_BYTES)
    args = ap.parse_args()

    db = SessionLocal()
    try:
        bad = (db.query(models.QuarterlyDocument)
                 .filter(models.QuarterlyDocument.file_size_bytes < args.threshold)
                 .all())
        print(f"Found {len(bad)} corrupt docs (< {args.threshold} bytes):")
        for d in bad:
            co = db.query(models.Company).filter_by(id=d.company_id).first()
            tk = co.ticker if co else f"id={d.company_id}"
            print(f"  {tk:12} {d.quarter:8} {d.doc_type:12} "
                  f"{d.file_size_bytes} bytes  key={d.r2_key}")

        if not bad:
            print("Nothing to clean.")
            return
        if args.dry_run:
            print("\n--dry-run set, nothing deleted.")
            return

        deleted_r2 = 0
        for d in bad:
            if d.r2_key:
                try:
                    delete_object(d.r2_key)
                    deleted_r2 += 1
                except Exception as e:
                    print(f"  ! R2 delete failed for {d.r2_key}: {e}")
            db.delete(d)
        db.commit()
        print(f"\n✓ Deleted {len(bad)} DB rows, {deleted_r2} R2 objects.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
