"""
reclassify.py — recompute each company's template_code (and type) using the
name-aware classifier, fixing banks that were mis-routed to NBFC because their
sector string is the generic 'Financial Services'.

Idempotent and safe: only updates template_code / type; never touches financials.

Run:
  python -m app.ingest.reclassify             # report + apply
  python -m app.ingest.reclassify --dry       # report only, no writes
  railway run python -m app.ingest.reclassify # against live Postgres
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from app.database import SessionLocal, engine, Base
from app import models
from app.templates import classify_company, FINANCIAL_TEMPLATES

Base.metadata.create_all(bind=engine)


def run(dry=False):
    db = SessionLocal()
    changed = 0
    try:
        for co in db.query(models.Company).all():
            new_tmpl = classify_company(co.name, co.sector)
            new_type = "financial" if new_tmpl in FINANCIAL_TEMPLATES else "nonfinancial"
            if new_tmpl != co.template_code or new_type != co.type:
                print(f"  {co.ticker:<12} {co.template_code or '—':<14} → {new_tmpl:<14} "
                      f"({co.type} → {new_type})   [{co.sector}]")
                if not dry:
                    co.template_code = new_tmpl
                    co.type = new_type
                changed += 1
        if not dry:
            db.commit()
        print(f"\n{'(dry run) ' if dry else ''}{changed} companies reclassified.")
    finally:
        db.close()


if __name__ == "__main__":
    run(dry="--dry" in sys.argv[1:])
