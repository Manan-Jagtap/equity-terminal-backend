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

  - Intraday spot prices         : every 90 min during NSE market hours
        → Nifty 50 prices via IndianAPI  (~50 calls/run, ~4 runs/market-day)

  Monthly budget ≈ 50×22 (EOD) + 200×22 (intraday) + 400×4 (weekly full)
                 ≈ 7,100 calls — within the 10,000/mo plan.

  (Intraday originally used a single batched Yahoo/yfinance call, but Yahoo
   blocks datacenter IPs — from Railway it returned 0/50 every run. IndianAPI is
   the only source that works server-side, so intraday now polls it every 90 min
   rather than every 15 min to stay quota-safe.)

Requires env var INDIANAPI_KEY on this service.
"""
import schedule, time, os, sys, logging
sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

from app.ingest.indianapi_ingester import run as indianapi_run, run_intraday, KEY
from app.ingest.compute_valuations import run as compute_valuations, refresh_mos
from app.ingest.reclassify import run as reclassify


def run_compute(nifty50=False):
    """Recompute the blended valuations (no external API — pure local computation
    from the data already in the DB). Cheap; safe to run after every refresh so
    the screener's intrinsic/MoS/verdict always reflect fresh prices.
    nifty50=True scopes it to the Nifty 50 (fast, upsert-in-place)."""
    log.info(f"Recomputing valuations ({'Nifty 50' if nifty50 else 'all'})…")
    try:
        compute_valuations(nifty50=nifty50)
        log.info("Valuation recompute complete.")
    except Exception as e:
        log.error(f"Valuation recompute failed: {e}")


def run_prices():
    log.info("IndianAPI daily price refresh (Nifty 50)…")
    try:
        indianapi_run(price_only=True, nifty50=True)
        run_compute()          # refresh MoS/verdict against the new prices
        log.info("Price refresh complete.")
    except Exception as e:
        log.error(f"Price refresh failed: {e}")


def run_intraday_prices():
    """Intraday spot-price refresh via IndianAPI (the yfinance batch path was
    abandoned — Yahoo blocks Railway's datacenter IP, so it returned 0/50 every
    run). IndianAPI works server-side but costs ~50 calls per run, so this fires
    at most every 90 min and only during NSE market hours (9:15-15:35 IST =
    03:45-10:05 UTC, Mon-Fri) to stay inside the monthly quota. After the price
    update, a CHEAP mos/verdict refresh (no DCF re-run — intrinsic doesn't move
    with price)."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return
    mins = now.hour * 60 + now.minute
    if not (3 * 60 + 45 <= mins <= 10 * 60 + 5):   # 03:45–10:05 UTC
        return
    try:
        n = run_intraday()
        if n:
            refresh_mos()                          # cheap: mos/verdict only
        log.info(f"Intraday price refresh: {n} prices updated (IndianAPI).")
    except Exception as e:
        log.error(f"Intraday price refresh failed: {e}")


def run_full():
    log.info("IndianAPI weekly full refresh (statements + facts + insights)…")
    try:
        indianapi_run(nifty50=True, insights=True)
        run_compute()          # rebuild valuations from the fresh statements
        log.info("Weekly full refresh complete.")
    except Exception as e:
        log.error(f"Weekly full refresh failed: {e}")


def run_bootstrap():
    """One-off, fully server-side bootstrap of the independent-DCF pipeline:
       1. reclassify   — fix templates (HDFC Bank → BANK, etc.)
       2. full ingest  — Nifty 50 statements + facts + insights (+ bank P&L)
       3. compute      — precompute independent valuations
    Triggered by RUN_BOOTSTRAP_NOW=true on the scheduler service."""
    log.info("BOOTSTRAP step 1/3 — reclassify companies…")
    try:
        reclassify()
    except Exception as e:
        log.error(f"reclassify failed: {e}")
    log.info("BOOTSTRAP step 2/3 — full Nifty 50 ingest…")
    try:
        indianapi_run(nifty50=True, insights=True)
    except Exception as e:
        log.error(f"ingest failed: {e}")
    log.info("BOOTSTRAP step 3/3 — compute independent valuations…")
    run_compute()
    log.info("BOOTSTRAP complete.")


if not KEY:
    log.warning("INDIANAPI_KEY is NOT set on this service — refreshes will no-op "
                "until you add it in Railway → equity-terminal-scheduler → Variables.")

# Daily EOD price refresh — 3:45pm IST = 10:15 UTC, Mon-Fri
for _day in ("monday", "tuesday", "wednesday", "thursday", "friday"):
    getattr(schedule.every(), _day).at("10:15").do(run_prices)

# Weekly full refresh — 6:00am IST Sunday = 00:30 UTC
schedule.every().sunday.at("00:30").do(run_full)

# Intraday spot prices — every 90 min; the job self-gates to NSE market hours.
# IndianAPI (~50 calls/run) since Yahoo blocks datacenter IPs: ~4 runs/market-day
# × 50 ≈ 200 calls/day → quota-safe alongside the daily EOD + weekly full.
schedule.every(90).minutes.do(run_intraday_prices)

log.info("Scheduler v3 (IndianAPI) started.")
log.info("Daily prices: 3:45pm IST Mon-Fri (10:15 UTC)")
log.info("Weekly full refresh: 6:00am IST Sunday (00:30 UTC)")
log.info("Intraday prices: every 90 min during NSE market hours (IndianAPI, ~50 calls/run)")

# ── One-off on-boot jobs (manual trigger from Railway, NO laptop) ────────────
# Set ONE of these to true on the scheduler service's Variables and redeploy.
# Remove the variable afterwards so it doesn't re-run on every restart.
#
#   RUN_BOOTSTRAP_NOW=true     → reclassify + full ingest + compute  (use this for
#                                the independent-DCF migration; the all-in-one)
#   RUN_FULL_NOW=true          → full ingest + compute only
#   RUN_COMPUTE_NIFTY50=true   → recompute valuations for the Nifty 50 ONLY (fast,
#                                upsert-in-place; no API calls). Use this to push
#                                the blended-valuation change live for our active
#                                universe without rebuilding the whole table.
#   RUN_COMPUTE_NOW=true       → recompute valuations for ALL companies (full rebuild)
#   RUN_REINGEST_TICKERS=A,B,C → re-pull statements+facts+price for ONLY these
#                                tickers from IndianAPI, then recompute. Use this
#                                to fix a single bad row (e.g. KOTAKBANK's split-
#                                scaled price/shares) without touching the rest.
#
_flag = lambda k: os.getenv(k, "").strip().lower() in ("1", "true", "yes")

_reingest = os.getenv("RUN_REINGEST_TICKERS", "").strip()
if _reingest:
    tickers = [t.strip().upper() for t in _reingest.split(",") if t.strip()]
    log.info(f"RUN_REINGEST_TICKERS set — re-ingesting {tickers} from IndianAPI…")
    for t in tickers:
        try:
            indianapi_run(ticker=t, insights=True)
            log.info(f"  re-ingested {t}")
        except Exception as e:
            log.error(f"  re-ingest {t} failed: {e}")
    run_compute(nifty50=True)
    log.info("Re-ingest + recompute done. Remove RUN_REINGEST_TICKERS from Variables now.")
elif _flag("RUN_BOOTSTRAP_NOW"):
    log.info("RUN_BOOTSTRAP_NOW set — running the full server-side bootstrap now…")
    try:
        run_bootstrap()
        log.info("Bootstrap done. Remove RUN_BOOTSTRAP_NOW from Variables now.")
    except Exception as e:
        log.error(f"Bootstrap failed: {e}")
elif _flag("RUN_COMPUTE_NIFTY50"):
    # Recompute valuations for the Nifty 50 ONLY — pure local computation, no
    # IndianAPI calls, no quota. Fast (~50 companies) and upserts in place.
    log.info("RUN_COMPUTE_NIFTY50 set — recomputing Nifty 50 valuations only (no API calls)…")
    try:
        run_compute(nifty50=True)
        log.info("Nifty 50 compute done. Remove RUN_COMPUTE_NIFTY50 from Variables now.")
    except Exception as e:
        log.error(f"Nifty 50 compute failed: {e}")
elif _flag("RUN_COMPUTE_NOW"):
    # Recompute valuations for ALL companies — pure local computation, no IndianAPI
    # calls, no quota. Full rebuild (drop + recreate) of the precomputed cache.
    log.info("RUN_COMPUTE_NOW set — recomputing ALL valuations (no API calls)…")
    try:
        run_compute()
        log.info("Compute done. Remove RUN_COMPUTE_NOW from Variables now.")
    except Exception as e:
        log.error(f"Compute failed: {e}")
elif _flag("RUN_INTRADAY_NOW"):
    # One-off test of the IndianAPI live-price path: fetch all 50, update prices,
    # cheap mos/verdict refresh.
    log.info("RUN_INTRADAY_NOW set — testing the IndianAPI live-price refresh…")
    try:
        n = run_intraday(debug=True)
        if n:
            refresh_mos()
        log.info(f"Intraday test: {n} prices updated. Remove RUN_INTRADAY_NOW now.")
    except Exception as e:
        log.error(f"Intraday test failed: {e}")
elif _flag("RUN_FULL_NOW"):
    log.info("RUN_FULL_NOW set — running a one-off FULL refresh now (server-side)…")
    try:
        run_full()
        log.info("One-off full refresh complete. "
                 "Remove RUN_FULL_NOW from Variables to avoid re-running on restart.")
    except Exception as e:
        log.error(f"One-off full refresh failed: {e}")

while True:
    schedule.run_pending()
    time.sleep(60)
