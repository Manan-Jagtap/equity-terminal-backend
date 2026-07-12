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


def snapshot_verdicts():
    """Append today's (verdict, price) row per company to the track-record
    ledger. Pure local write — no API calls. Idempotent per (company, date)."""
    try:
        from app.backtest import take_snapshots
        from app.database import SessionLocal, engine
        from app import models
        models.VerdictSnapshot.__table__.create(bind=engine, checkfirst=True)
        s = SessionLocal()
        try:
            n = take_snapshots(s)
            log.info(f"Track record: snapshotted {n} verdicts.")
        finally:
            s.close()
    except Exception as e:
        log.error(f"Verdict snapshot failed: {type(e).__name__}: {e}")


def snapshot_signals():
    """Append today's Alpha-Score ranking + analyst-consensus rows to their
    ledgers — the factor track record and estimate-revision history (neither can
    be backfilled). Pure local write, idempotent per (company, date)."""
    try:
        from app import models, signals
        from app.database import SessionLocal, engine
        models.AlphaSnapshot.__table__.create(bind=engine, checkfirst=True)
        models.ConsensusSnapshot.__table__.create(bind=engine, checkfirst=True)
        s = SessionLocal()
        try:
            na = signals.snapshot_alpha(s)
            nc = signals.snapshot_consensus(s)
            log.info(f"Signals: snapshotted {na} alpha + {nc} consensus rows.")
        finally:
            s.close()
    except Exception as e:
        log.error(f"Signal snapshot failed: {type(e).__name__}: {e}")


def run_compute(nifty50=False, visible=False):
    """Recompute the blended valuations (no external API — pure local computation
    from the data already in the DB). Cheap; safe to run after every refresh so
    the screener's intrinsic/MoS/verdict always reflect fresh prices.
    nifty50=True → Nifty 50; visible=True → the Nifty 100 the terminal exposes."""
    scope = "Nifty 100" if visible else "Nifty 50" if nifty50 else "all"
    log.info(f"Recomputing valuations ({scope})…")
    try:
        compute_valuations(nifty50=nifty50, visible=visible)
        log.info("Valuation recompute complete.")
    except Exception as e:
        log.error(f"Valuation recompute failed: {e}")


def run_dhan_topup(days: int = 30):
    """Incremental Dhan top-up of HistoricalPrice for the visible universe so the
    5-yr series stays current (the one-off RUN_DHAN_BACKFILL seeded it; this keeps
    it fed). ~1 REST call/name, self-rate-limited, no WebSocket — recorder-safe.
    The 30-day window self-heals holiday/outage gaps. No-op when Dhan is unset."""
    try:
        from app.dhan import client as _dhan
        from app.dhan.backfill import backfill_prices
        from app.ingest.indianapi_ingester import VISIBLE_UNIVERSE
        from app.database import SessionLocal
        if not _dhan.configured():
            return
        s = SessionLocal()
        try:
            stats = backfill_prices(s, sorted(VISIBLE_UNIVERSE), days=days)
            # Mark names IndianAPI's core EOD pass doesn't cover (wider tiers)
            # to Dhan's close, so nothing visible drifts on a stale price.
            from app.dhan.backfill import sync_snapshots_from_history
            sync = sync_snapshots_from_history(s, sorted(VISIBLE_UNIVERSE))
        finally:
            s.close()
        log.info(f"Dhan daily top-up: {stats} · snapshot sync: {sync}")
    except Exception as e:
        log.error(f"Dhan daily top-up failed: {e}")


def run_prices():
    # Daily EOD prices for the FULL visible set (Nifty 100) so every shown name is
    # marked to today's close; intraday + weekly full stay Nifty-50 tight for quota.
    log.info("IndianAPI daily price refresh (Nifty 100)…")
    try:
        indianapi_run(price_only=True, visible=True)
        run_dhan_topup()            # keep the Dhan 5-yr HistoricalPrice series current
        run_compute(visible=True)   # refresh MoS/verdict against the new prices
        snapshot_verdicts()         # append today's calls to the track record
        snapshot_signals()          # alpha + consensus ledgers (factor track record)
        log.info("Price refresh complete.")
    except Exception as e:
        log.error(f"Price refresh failed: {e}")


def run_intraday_prices():
    """Intraday spot-price refresh. Primary path: ONE Dhan batch-LTP call marks
    every visible name (500 snapshots, 1 request, zero IndianAPI quota — this
    replaced the 50-name IndianAPI poll and freed ~4,400 calls/month for the
    rolling fundamentals cohort). IndianAPI per-name polling remains only as
    the fallback when Dhan is unconfigured. Fires every 90 min during NSE
    hours; the /api/live endpoint gives clients ~12s freshness in between.
    After the price update, a CHEAP mos/verdict refresh (no DCF re-run)."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return
    mins = now.hour * 60 + now.minute
    if not (3 * 60 + 45 <= mins <= 10 * 60 + 5):   # 03:45–10:05 UTC
        return
    try:
        from app.dhan import client as _dhan
        if _dhan.configured():
            from app.live_prices import update_snapshots_from_live
            from app.database import SessionLocal
            s = SessionLocal()
            try:
                n = update_snapshots_from_live(s)
            finally:
                s.close()
            src = "Dhan batch LTP"
        else:
            n = run_intraday()
            src = "IndianAPI fallback"
        if n:
            refresh_mos()                          # cheap: mos/verdict only
        log.info(f"Intraday price refresh: {n} prices updated ({src}).")
    except Exception as e:
        log.error(f"Intraday price refresh failed: {e}")


def rolling_cohort(size: int | None = None) -> list:
    """This week's slice of the non-core visible universe for the rotating full
    refresh. Deterministic from the ISO week number — no cursor to persist, and
    a redeploy mid-week re-picks the same cohort (idempotent re-ingest). At the
    default 60 names/week the ~450 non-core names fully cycle in ~8 weeks;
    fundamentals move quarterly, so that keeps everyone inside one quarter.
    Tune with ROLLING_REFRESH_SIZE (0 disables)."""
    import datetime as _dt
    from app.ingest.indianapi_ingester import VISIBLE_UNIVERSE, UNIVERSE
    size = int(os.getenv("ROLLING_REFRESH_SIZE", "60")) if size is None else size
    names = sorted(VISIBLE_UNIVERSE - UNIVERSE)
    if size <= 0 or not names:
        return []
    week = _dt.date.today().isocalendar()[1]
    start = (week * size) % len(names)
    picked = names[start:start + size]
    if len(picked) < size:                     # wrap around the list end
        picked += names[:size - len(picked)]
    return picked


def run_full():
    # Core Nifty 50: full statements + facts + insights weekly (as always).
    # Then this week's rolling cohort of the wider universe gets the same
    # treatment, so analyst consensus / documents / forecasts exist for ALL
    # 500 names, not just the core — the budget pre-flight in run() aborts
    # the cohort if the month's IndianAPI budget would be breached.
    log.info("IndianAPI weekly full refresh (statements + facts + insights)…")
    try:
        indianapi_run(nifty50=True, insights=True)
        cohort = rolling_cohort()
        if cohort:
            log.info(f"Rolling cohort refresh: {len(cohort)} names "
                     f"({cohort[0]}…{cohort[-1]})")
            indianapi_run(tickers=cohort, insights=True)
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
elif _flag("RUN_PROBE_ENDPOINTS"):
    # One-off shape probe for the not-yet-leveraged IndianAPI endpoints. Prints
    # compact response shapes to the logs; writes nothing. Remove the flag after.
    log.info("RUN_PROBE_ENDPOINTS set — probing unused IndianAPI endpoints…")
    try:
        from app.ingest.endpoint_probe import run as probe_new
        probe_new()
        log.info("Endpoint probe done. Remove RUN_PROBE_ENDPOINTS from Variables now.")
    except Exception as e:
        log.error(f"Endpoint probe failed: {e}")
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
elif _flag("RUN_DHAN_BACKFILL"):
    # One-off: populate HistoricalPrice from Dhan daily OHLCV (REST-only — no
    # WebSocket, so the recorder's feeds are untouched). Scope with
    # RUN_DHAN_TICKERS=A,B,C or default to the visible universe. Needs
    # DHAN_ACCESS_TOKEN set. Remove the flag after it runs.
    log.info("RUN_DHAN_BACKFILL set — backfilling HistoricalPrice from Dhan (REST)…")
    try:
        from app.dhan.backfill import backfill_prices
        from app.dhan import client as _dhan
        from app.ingest.indianapi_ingester import VISIBLE_UNIVERSE
        from app.database import SessionLocal
        if not _dhan.configured():
            log.error("DHAN_ACCESS_TOKEN not set — nothing to backfill.")
        else:
            _scope = os.getenv("RUN_DHAN_TICKERS", "").strip()
            _tickers = ([t.strip().upper() for t in _scope.split(",") if t.strip()]
                        if _scope else sorted(VISIBLE_UNIVERSE))
            s = SessionLocal()
            try:
                stats = backfill_prices(s, _tickers)
                log.info(f"Dhan backfill result: {stats}")
            finally:
                s.close()
            run_compute(visible=True)   # recompute so charts/verdicts see fresh history
        log.info("Dhan backfill done. Remove RUN_DHAN_BACKFILL from Variables now.")
    except Exception as e:
        log.error(f"Dhan backfill failed: {type(e).__name__}: {e}")
elif _flag("RUN_FUNDAMENTALS_BACKFILL"):
    # One-off: full statements+facts+insights ingest for EXACTLY the visible
    # names whose Financials or Analyst & Forward tabs would be empty (fewer
    # than 4 statement rows, or no insight blob). The budget pre-flight in
    # run() sizes the batch and refuses overruns. Remove the flag after.
    log.info("RUN_FUNDAMENTALS_BACKFILL set — filling empty Financials/Analyst tabs…")
    try:
        from app.coverage import needs_fundamentals
        from app.ingest.indianapi_ingester import VISIBLE_UNIVERSE
        from app.database import SessionLocal
        s = SessionLocal()
        try:
            targets = needs_fundamentals(s, VISIBLE_UNIVERSE)
        finally:
            s.close()
        if not targets:
            log.info("Fundamentals backfill: nothing missing — all tabs covered.")
        else:
            log.info(f"Fundamentals backfill: {len(targets)} names "
                     f"({targets[0]}…{targets[-1]})")
            indianapi_run(tickers=targets, insights=True)
            run_compute()
        log.info("Fundamentals backfill done. Remove RUN_FUNDAMENTALS_BACKFILL now.")
    except Exception as e:
        log.error(f"Fundamentals backfill failed: {type(e).__name__}: {e}")
elif _flag("RUN_DHAN_REPAIR"):
    # One-off: fix the 2026-07-04 UTC-shifted-date poisoning END TO END.
    # 1) wipe + refill companies whose history holds Sunday-dated rows (the
    #    buggy converter's signature — 21 names as of the incident scan),
    # 2) full backfill over the visible universe (fills the ~40 names the June
    #    run never reached and resumes past the crash point),
    # 3) recompute. Needs DHAN_ACCESS_TOKEN. Remove the flag after it runs.
    log.info("RUN_DHAN_REPAIR set — repairing UTC-shifted price histories…")
    try:
        from app.dhan.backfill import repair_shifted_histories, backfill_prices
        from app.dhan import client as _dhan
        from app.ingest.indianapi_ingester import VISIBLE_UNIVERSE
        from app.database import SessionLocal
        if not _dhan.configured():
            log.error("DHAN_ACCESS_TOKEN not set — cannot re-backfill after wiping; aborting.")
        else:
            s = SessionLocal()
            try:
                stats = repair_shifted_histories(s)
                log.info(f"Dhan repair result: {stats}")
                full = backfill_prices(s, sorted(VISIBLE_UNIVERSE))
                log.info(f"Dhan full backfill result: {full}")
            finally:
                s.close()
            run_compute(visible=True)   # recompute so factors/charts see clean history
        log.info("Dhan repair done. Remove RUN_DHAN_REPAIR from Variables now.")
    except Exception as e:
        log.error(f"Dhan repair failed: {type(e).__name__}: {e}")
else:
    # ── Auto-onboard missing universe members on every deploy ────────────────
    # Adding a ticker to EXTRA_TICKERS (indianapi_ingester.py) is the ONLY step
    # needed to cover a new name: on boot we create its Company row if missing
    # and ingest statements+facts+price for any universe member that has no
    # market snapshot yet. Each onboarding costs a handful of IndianAPI calls,
    # and only ever runs for genuinely missing names (idempotent).
    def _ensure_universe():
        # Ensure every VISIBLE (Nifty 100) member has a row + market data. They are
        # already ingested today, so this is a no-op unless a new name is added to
        # NIFTY_NEXT_50 — in which case onboarding backfills its real sector/class.
        from app.ingest.indianapi_ingester import VISIBLE_UNIVERSE as UNIVERSE, ingest_company
        from app.database import SessionLocal
        from app import models
        s = SessionLocal()
        try:
            by_ticker = {(c.ticker or "").upper(): c for c in s.query(models.Company).all()}
            missing = sorted(UNIVERSE - set(by_ticker))
            for t in missing:           # create the row so ingest can fill it
                log.info(f"Universe: creating missing company row {t}…")
                # NON-NULL placeholders: companies.sector and shares_outstanding are
                # NOT NULL on Postgres, so `sector=None` would raise on insert. The
                # "Unknown" sentinel is what ingest_company._resolve_onboarding keys
                # off to backfill the real name/sector/template on first ingest.
                co = models.Company(
                    ticker=t,
                    name=t.title(),
                    type="financial" if t == "FEDFINA" else "nonfinancial",
                    sector="Diversified NBFC" if t == "FEDFINA" else "Unknown",
                    shares_outstanding=0.0,
                )
                s.add(co); s.commit()
                by_ticker[t] = co
            snap_ids = {m.company_id for m in s.query(models.MarketSnapshot).all()}
            for t in sorted(UNIVERSE):
                co = by_ticker.get(t)
                if co is not None and co.id not in snap_ids:
                    log.info(f"Universe: ingesting {t} (no market data yet)…")
                    try:
                        ingest_company(s, co, insights=True)
                        log.info(f"  {t} ingested.")
                    except Exception as e:
                        log.error(f"  {t} ingest failed: {type(e).__name__}: {e}")
        finally:
            s.close()

    try:
        _ensure_universe()
    except Exception as e:
        log.error(f"Universe check failed: {type(e).__name__}: {e}")

    # ── Auto-recompute on every deploy ───────────────────────────────────────
    # The valuations cache is a pure local computation from data already in the
    # DB (no API calls, no quota). Recomputing it on boot means every engine /
    # sector-param change goes live the moment the scheduler redeploys — no
    # dashboard flags, no manual steps. Disable with COMPUTE_ON_BOOT=false.
    if os.getenv("COMPUTE_ON_BOOT", "true").strip().lower() not in ("0", "false", "no"):
        log.info("Deploy boot — recomputing valuations so the cache matches the current engine…")
        run_compute()
        log.info("Boot recompute complete.")
        snapshot_verdicts()    # day-0 (and post-deploy) track-record entries
        snapshot_signals()     # day-0 alpha + consensus ledgers

while True:
    schedule.run_pending()
    time.sleep(60)
