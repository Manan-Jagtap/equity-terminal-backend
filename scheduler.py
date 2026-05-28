"""
Scheduler — runs as a separate Railway service.
Prices refresh daily at market close (Mon-Fri 15:45 IST = 10:15 UTC).
Fundamentals refresh every Sunday at 6am IST (00:30 UTC).
"""
import schedule, time, os, sys
sys.path.insert(0, os.path.dirname(__file__))

from app.database import SessionLocal
from app.ingest.price_ingester import ingest_prices
from app.ingest.fundamentals_ingester import ingest_fundamentals

def run_prices():
    print("Running daily price refresh...")
    db = SessionLocal()
    try:
        ingest_prices(db)
        print("Price refresh complete.")
    finally:
        db.close()

def run_fundamentals():
    print("Running weekly fundamentals refresh...")
    db = SessionLocal()
    try:
        ingest_fundamentals(db)
        print("Fundamentals refresh complete.")
    finally:
        db.close()

# Mon-Fri at 10:15 UTC = 15:45 IST (just after market close)
schedule.every().monday.at("10:15").do(run_prices)
schedule.every().tuesday.at("10:15").do(run_prices)
schedule.every().wednesday.at("10:15").do(run_prices)
schedule.every().thursday.at("10:15").do(run_prices)
schedule.every().friday.at("10:15").do(run_prices)

# Every Sunday at 00:30 UTC = 06:00 IST
schedule.every().sunday.at("00:30").do(run_fundamentals)

print("Scheduler started. Waiting for next run...")
while True:
    schedule.run_pending()
    time.sleep(60)
