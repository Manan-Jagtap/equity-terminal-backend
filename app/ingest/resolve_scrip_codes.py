"""
resolve_scrip_codes.py — fill bse_scrip_code for every company using the
`bse` library's getScripCode (Phase 0 of the XBRL pipeline).

Idempotent: only resolves companies missing a code. Resilient: fresh BSE
session, per-company try/except, polite delay.

Run:
  python -m app.ingest.resolve_scrip_codes
  python -m app.ingest.resolve_scrip_codes --all   # re-resolve everyone
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from app.database import SessionLocal
from app import models

try:
    from bse import BSE
except ImportError:
    print("ERROR: pip install bse"); sys.exit(1)


def run(force_all=False):
    db = SessionLocal()
    bse = BSE(download_folder="/tmp")
    try:
        q = db.query(models.Company)
        companies = q.all()
        todo = [c for c in companies if force_all or not c.bse_scrip_code]
        print(f"{len(companies)} companies, {len(todo)} need scrip codes")
        ok = miss = 0
        for i, co in enumerate(todo, 1):
            try:
                code = bse.getScripCode(co.ticker.lower())
                if code:
                    co.bse_scrip_code = str(code)
                    db.commit()
                    ok += 1
                    if i % 25 == 0:
                        print(f"  ...{i}/{len(todo)}  ({ok} found)")
                else:
                    miss += 1
                    print(f"  {co.ticker}: no scrip code found")
            except Exception as e:
                db.rollback()
                miss += 1
                print(f"  {co.ticker}: ERROR {e}")
            time.sleep(0.4)  # polite
        print(f"\nDone. Resolved: {ok}  Missing: {miss}")
        total_with = db.query(models.Company).filter(
            models.Company.bse_scrip_code.isnot(None)).count()
        print(f"Total companies with scrip code now: {total_with}")
    finally:
        try: bse.exit()
        except Exception: pass
        db.close()


if __name__ == "__main__":
    run(force_all="--all" in sys.argv)
