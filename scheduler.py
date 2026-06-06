"""
scheduler.py — upgraded to 15-minute price refresh during market hours.

Schedule:
- Every 15 mins between 9:15am and 3:30pm IST (Mon-Fri) = prices refresh during market hours
- Once daily at 3:45pm IST (Mon-Fri) = end-of-day full refresh
- Every Sunday at 6:00am IST = weekly fundamentals refresh

IST = UTC + 5:30
So:
  9:15 IST  = 03:45 UTC
  3:30 IST  = 10:00 UTC
  3:45 IST  = 10:15 UTC
"""
import schedule, time, os, sys, logging
sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

from app.database import SessionLocal
from app.ingest.price_ingester import ingest_prices
from app.ingest.fundamentals_ingester import ingest_fundamentals
from app.ingest.statements_ingester import ingest_statements


def run_prices():
    log.info("Running price refresh...")
    db = SessionLocal()
    try:
        ingest_prices(db)
        log.info("Price refresh complete.")
    except Exception as e:
        log.error(f"Price refresh failed: {e}")
    finally:
        db.close()


def run_fundamentals():
    log.info("Running weekly fundamentals + statements refresh...")
    db = SessionLocal()
    try:
        ingest_fundamentals(db)
        log.info("Fundamentals refresh complete.")
    except Exception as e:
        log.error(f"Fundamentals refresh failed: {e}")
    finally:
        db.close()
    # Multi-year statements (powers the Financials tab and growth/CAGR ratios).
    # Uses its own per-company sessions, so pass nothing.
    try:
        ingest_statements()
        log.info("Statements refresh complete.")
    except Exception as e:
        log.error(f"Statements refresh failed: {e}")


# ── Intraday: every 15 mins, Mon-Fri, 9:15am–3:30pm IST (03:45–10:00 UTC) ──
# We schedule every 15 mins and check if we're inside market hours
def run_prices_if_market_open():
    """Only runs if current UTC time is within NSE market hours."""
    import datetime
    now_utc = datetime.datetime.utcnow()
    weekday = now_utc.weekday()  # 0=Mon, 4=Fri, 5=Sat, 6=Sun

    if weekday >= 5:  # Saturday or Sunday — market closed
        return

    # NSE market hours: 9:15 IST to 3:30 IST = 3:45 UTC to 10:00 UTC
    market_open  = now_utc.replace(hour=3, minute=45, second=0, microsecond=0)
    market_close = now_utc.replace(hour=10, minute=0,  second=0, microsecond=0)

    if market_open <= now_utc <= market_close:
        log.info("Market open — running 15-min price refresh")
        run_prices()
    else:
        log.info(f"Market closed at {now_utc.strftime('%H:%M')} UTC — skipping intraday refresh")


# Every 15 minutes — guard function checks if market is open
schedule.every(15).minutes.do(run_prices_if_market_open)

# End-of-day full refresh — 3:45pm IST = 10:15 UTC, Mon-Fri
schedule.every().monday.at("10:15").do(run_prices)
schedule.every().tuesday.at("10:15").do(run_prices)
schedule.every().wednesday.at("10:15").do(run_prices)
schedule.every().thursday.at("10:15").do(run_prices)
schedule.every().friday.at("10:15").do(run_prices)

# Weekly fundamentals — 6:00am IST Sunday = 00:30 UTC
schedule.every().sunday.at("00:30").do(run_fundamentals)

log.info("Scheduler v2 started.")
log.info("Intraday: every 15 mins during NSE market hours (9:15am-3:30pm IST, Mon-Fri)")
log.info("End-of-day: 3:45pm IST Mon-Fri")
log.info("Fundamentals: 6:00am IST Sunday")

while True:
    schedule.run_pending()
    time.sleep(60)
