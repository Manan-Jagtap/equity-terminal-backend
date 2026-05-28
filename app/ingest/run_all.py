"""
run_all.py — runs all ingesters in the correct order.

Usage:
  python -m app.ingest.run_all           # full refresh
  python -m app.ingest.run_all --prices  # prices only (run daily)
  python -m app.ingest.run_all --fundamentals  # fundamentals only (run weekly)

Schedule on Railway:
  Add a second service (cron job) or use Railway's built-in cron:
  Cron expression "0 18 * * 1-5" = every weekday at 6pm IST (market close)
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from app.database import SessionLocal
from app.ingest.price_ingester import ingest_prices
from app.ingest.fundamentals_ingester import ingest_fundamentals
from app.ingest.bse_results_ingester import ingest_bse_results, update_nbfc_metrics_manually


def run_all(prices=True, fundamentals=True, bse=True):
    db = SessionLocal()
    try:
        if prices:
            print("=" * 50)
            print("STEP 1: Prices + OHLC from Yahoo Finance")
            print("=" * 50)
            ingest_prices(db)

        if fundamentals:
            print()
            print("=" * 50)
            print("STEP 2: Fundamentals from Yahoo Finance")
            print("=" * 50)
            ingest_fundamentals(db)

        if bse:
            print()
            print("=" * 50)
            print("STEP 3: BSE results + NBFC metrics")
            print("=" * 50)
            ingest_bse_results(db)
            update_nbfc_metrics_manually(db)

        print()
        print("All ingestion complete. Your platform now has real data.")
    finally:
        db.close()


if __name__ == "__main__":
    args = sys.argv[1:]
    run_all(
        prices="--prices" in args or not args,
        fundamentals="--fundamentals" in args or not args,
        bse="--bse" in args or not args,
    )
