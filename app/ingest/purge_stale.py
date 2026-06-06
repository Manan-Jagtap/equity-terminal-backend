"""
purge_stale.py — ONE-TIME cleanup. Deletes every HistoricalFinancial row that
is NOT sourced from IndianAPI (leftover yfinance / seed rows that inflated the
year counts to 7–10 and fed stale numbers into the Ratios & Peers tabs).

This is a single DELETE — no API calls, no re-ingestion. Takes a few seconds.

Run once:
  railway run python3 -m app.ingest.purge_stale
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from sqlalchemy import func
from app.database import SessionLocal
from app import models


def run():
    s = SessionLocal()
    try:
        before = dict(s.query(models.HistoricalFinancial.source, func.count())
                       .group_by(models.HistoricalFinancial.source).all())
        print("Sources before:", before)

        n = (s.query(models.HistoricalFinancial)
               .filter(models.HistoricalFinancial.source != "indianapi")
               .delete(synchronize_session=False))
        s.commit()

        after = dict(s.query(models.HistoricalFinancial.source, func.count())
                      .group_by(models.HistoricalFinancial.source).all())
        print(f"\n✓ Deleted {n} stale (non-IndianAPI) statement rows.")
        print("Sources after:", after)
    finally:
        s.close()


if __name__ == "__main__":
    run()
