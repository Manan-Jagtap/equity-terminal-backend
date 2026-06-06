"""
scheduler.py — IndianAPI-powered refresh (v3).

Replaces the old yfinance refresh entirely. The yfinance ingesters were the
inaccurate source we migrated away from; running them here would overwrite the
accurate IndianAPI data, so they are no longer called.

Schedule (IST = UTC + 5:30):
  - Daily end-of-day price refresh : Mon-Fri 3:45pm IST (10:15 UTC)
        → Nifty 50 prices via IndianAPI  (~50 calls/day)
  - Weekly full refresh            : Sunday 6:00am IST (00:30 UTC)
        → Nifty 50 statements + facts + insights  (~400 calls)

  Monthly budget ≈ 50×22 + 400×4 ≈ 2,700 calls — well within the 10,000/mo plan.

  (The 15-minute intraday refresh is intentionally deferred until the
   /nse_stock_batch_live_price endpoint is wired in Phase 2 — that refreshes all
   50 in ONE call, so intraday becomes quota-safe. Per-company intraday polling
   would blow the monthly quota, so we don't do it.)

Requires env var INDIANAPI_KEY on this service.
"""
import schedule, time, os, sys, logging
sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

from app.ingest.indianapi_ingester import run as indianapi_run, KEY


def run_prices():
    log.info("IndianAPI daily price refresh (Nifty 50)…")
    try:
        indianapi_run(price_only=True, nifty50=True)
        log.info("Price refresh complete.")
    except Exception as e:
        log.error(f"Price refresh failed: {e}")


def run_full():
    log.info("IndianAPI weekly full refresh (statements + facts + insights)…")
    try:
        indianapi_run(nifty50=True, insights=True)
        log.info("Weekly full refresh complete.")
    except Exception as e:
        log.error(f"Weekly full refresh failed: {e}")


if not KEY:
    log.warning("INDIANAPI_KEY is NOT set on this service — refreshes will no-op "
                "until you add it in Railway → equity-terminal-scheduler → Variables.")

# Daily EOD price refresh — 3:45pm IST = 10:15 UTC, Mon-Fri
for _day in ("monday", "tuesday", "wednesday", "thursday", "friday"):
    getattr(schedule.every(), _day).at("10:15").do(run_prices)

# Weekly full refresh — 6:00am IST Sunday = 00:30 UTC
schedule.every().sunday.at("00:30").do(run_full)

log.info("Scheduler v3 (IndianAPI) started.")
log.info("Daily prices: 3:45pm IST Mon-Fri (10:15 UTC)")
log.info("Weekly full refresh: 6:00am IST Sunday (00:30 UTC)")

while True:
    schedule.run_pending()
    time.sleep(60)
