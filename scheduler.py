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
            stats = backfill_prices(s, universe_tickers(s), days=days)
            # Mark names IndianAPI's core EOD pass doesn't cover (wider tiers)
            # to Dhan's close, so nothing visible drifts on a stale price.
            from app.dhan.backfill import sync_snapshots_from_history
            sync = sync_snapshots_from_history(s, universe_tickers(s))
        finally:
            s.close()
        log.info(f"Dhan daily top-up: {stats} · snapshot sync: {sync}")
        # Dhan outage escalation: if the top-up added nothing AND synced
        # nothing (dead/expired token), spend IndianAPI budget pricing the
        # WIDER tier directly — this is exactly what the monthly budget guard
        # exists for. Core names are already covered by the daily EOD pass.
        if not stats.get("ok") and not sync.get("updated"):
            log.warning("Dhan appears down — escalating IndianAPI EOD to the full visible universe.")
            from app.ingest.indianapi_ingester import CORE_UNIVERSE
            wider = sorted(set(VISIBLE_UNIVERSE) - set(CORE_UNIVERSE))
            if wider:
                indianapi_run(price_only=True, tickers=wider)
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
        n, src = 0, "none"
        if _dhan.configured():
            from app.live_prices import update_snapshots_from_live
            from app.database import SessionLocal
            s = SessionLocal()
            try:
                n = update_snapshots_from_live(s)
            except Exception as e:
                log.warning(f"Dhan intraday failed ({e}); falling back to IndianAPI.")
            finally:
                s.close()
            src = "Dhan batch LTP"
        if n == 0:
            # HEALTH-based fallback, not presence-based: an expired token is
            # "configured" but useless — the 8-day July outage proved a
            # presence check silently disables the backup vendor.
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



def run_results_calendar():
    """Weekly sweep of /corporate_actions board meetings for the whole visible
    universe → CompanyInsight.data['board_meetings'] (the Results page's
    'Upcoming' calendar). ~500 calls/week — comfortably inside the 40k/month
    Growth budget, and pre-flighted anyway."""
    try:
        import re as _re
        from app.database import SessionLocal
        from app import models, api_budget
        from app.ingest.indianapi_ingester import VISIBLE_UNIVERSE, _get_safe
        from sqlalchemy.orm.attributes import flag_modified
        s = SessionLocal()
        wrote = 0
        try:
            if api_budget.would_exceed(s, len(VISIBLE_UNIVERSE)):
                log.info("Results calendar: budget pre-flight refused — skipping.")
                return
            for tk in universe_tickers(s):
                co = s.query(models.Company).filter_by(ticker=tk).first()
                if co is None:
                    continue
                r = _get_safe("/corporate_actions", {"stock_name": tk})
                bm = (r or {}).get("board_meetings") if isinstance(r, dict) else None
                rows = (bm or {}).get("data") or []
                meetings = [{"date": str(row[0]), "agenda": str(row[1])[:300]}
                            for row in rows if isinstance(row, (list, tuple)) and len(row) >= 2][:8]
                if not meetings:
                    continue
                ins = s.query(models.CompanyInsight).filter_by(company_id=co.id).first()
                if ins is None:
                    ins = models.CompanyInsight(company_id=co.id, data={})
                    s.add(ins)
                data = dict(ins.data or {})
                data["board_meetings"] = meetings
                ins.data = data
                flag_modified(ins, "data")
                s.commit()
                wrote += 1
            api_budget.record_usage(s, len(VISIBLE_UNIVERSE))
        finally:
            s.close()
        log.info(f"Results calendar: board meetings stored for {wrote} names.")
    except Exception as e:
        log.error(f"Results calendar failed: {type(e).__name__}: {e}")



def universe_tickers(s):
    """The FULL living universe for refresh jobs: the static tier plus every
    company already onboarded (monthly refresh adds IPOs/new entrants as DB
    rows, and they must not fall out of the daily/weekly cycles)."""
    from app import models
    from app.ingest.indianapi_ingester import VISIBLE_UNIVERSE
    db_tickers = {(c.ticker or "").upper() for c in s.query(models.Company.ticker).all()}
    return sorted((VISIBLE_UNIVERSE | db_tickers) - {""})


def run_universe_refresh():
    """Monthly: keep the universe current as markets move.
    1) New IPO listings (vendor /ipo, mainboard only) that clear the tier's
       ~₹2,600cr mcap floor are onboarded automatically.
    2) Official index CSVs (niftyindices.com) are re-fetched and any NEW
       constituents onboarded (index rebalances land in Mar/Sep; the AMFI
       re-rank lands Jan/Jul and is reviewed with the same run).
    Removals are LOGGED, never auto-deleted — dropping a company is a human
    decision. Budget pre-flighted; each onboarding ≈ 10 vendor calls."""
    import requests as _rq
    from app.database import SessionLocal
    from app import models, api_budget
    from app.ingest.indianapi_ingester import (VISIBLE_UNIVERSE, ingest_company,
                                               _get_safe, BASE as _IA_BASE, KEY as _IA_KEY)
    s = SessionLocal()
    added = []
    try:
        have = {(c.ticker or "").upper() for c in s.query(models.Company.ticker).all()}

        # ── 1) IPO graduates ─────────────────────────────────────────────
        candidates = []
        try:
            r = _rq.get(_IA_BASE + "/ipo", headers={"x-api-key": _IA_KEY}, timeout=25)
            for row in (r.json() or {}).get("listed") or []:
                sym = str(row.get("symbol") or "").upper().strip()
                if sym and not row.get("is_sme") and sym not in have:
                    candidates.append(sym)
        except Exception as e:
            log.warning(f"Universe refresh: IPO feed failed — {e}")

        # ── 2) Official index CSV deltas ─────────────────────────────────
        for url in ("https://niftyindices.com/IndexConstituent/ind_nifty500list.csv",
                    "https://niftyindices.com/IndexConstituent/ind_niftymicrocap250_list.csv"):
            try:
                r = _rq.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=25)
                if r.status_code != 200:
                    continue
                import csv as _csv, io as _io
                for row in _csv.DictReader(_io.StringIO(r.text)):
                    sym = (row.get("Symbol") or "").upper().strip()
                    if sym and sym not in have and sym not in candidates:
                        candidates.append(sym)
            except Exception as e:
                log.warning(f"Universe refresh: {url.rsplit('/',1)[-1]} failed — {e}")

        if not candidates:
            log.info("Universe refresh: no new entrants this month.")
            return
        if api_budget.would_exceed(s, len(candidates) * 10):
            log.info(f"Universe refresh: {len(candidates)} candidates exceed budget — skipping.")
            return
        log.info(f"Universe refresh: onboarding {len(candidates)} new entrants: {candidates[:12]}…")
        for sym in candidates[:40]:      # hard cap per run — sanity over surprise
            try:
                co = s.query(models.Company).filter_by(ticker=sym).first()
                if co is None:
                    co = models.Company(ticker=sym, name=sym, sector="",
                                        shares_outstanding=0, type="nonfinancial")
                    s.add(co)
                    s.commit()
                if ingest_company(s, co, insights=True):
                    added.append(sym)
            except Exception as e:
                log.warning(f"  {sym}: onboarding failed — {type(e).__name__}: {e}")
                s.rollback()
        log.info(f"Universe refresh done: {len(added)} onboarded ({added}).")
        if added:
            run_compute(visible=True)
    finally:
        s.close()

def run_manager_evidence():
    """Nightly Fund Manager v4 evidence build: per-name triangulation inputs
    (forensics, P/E band, consensus, flow, momentum) + the macro regime block
    → KVStore, so /api/portfolio/analysis stays instant."""
    try:
        from app.database import SessionLocal
        from app.manager_engine import snapshot_evidence
        s = SessionLocal()
        try:
            log.info(f"FM evidence: {snapshot_evidence(s)}")
        finally:
            s.close()
    except Exception as e:
        log.error(f"FM evidence failed: {type(e).__name__}: {e}")


def run_manager_calibration():
    """Monthly signal-IC calibration on our own 5-yr full-universe history —
    the Fund Manager's weights come from measured evidence, not vibes."""
    try:
        from app.database import SessionLocal
        from app.manager_calibration import run_calibration
        s = SessionLocal()
        try:
            art = run_calibration(s)
            log.info(f"FM calibration: ic={art.get('ic')} weights={art.get('weights')}")
        finally:
            s.close()
    except Exception as e:
        log.error(f"FM calibration failed: {type(e).__name__}: {e}")


# Daily EOD price refresh — 3:45pm IST = 10:15 UTC, Mon-Fri
for _day in ("monday", "tuesday", "wednesday", "thursday", "friday"):
    getattr(schedule.every(), _day).at("10:15").do(run_prices)

# FM v4 evidence — nightly after the EOD refresh settles (4:45pm IST = 11:15 UTC)
for _day in ("monday", "tuesday", "wednesday", "thursday", "friday"):
    getattr(schedule.every(), _day).at("11:15").do(run_manager_evidence)

# FM v4 calibration — monthly, first Saturday 02:30 IST (= Fri 21:00 UTC)
def _monthly_manager_calibration():
    import datetime as _dt
    if _dt.date.today().day <= 7:
        run_manager_calibration()
schedule.every().friday.at("21:00").do(_monthly_manager_calibration)


def run_missing_history_backfill():
    """Self-healing price history: any onboarded name with (almost) no
    HistoricalPrice rows gets its 5-yr Dhan seed + a snapshot sync. Catches
    tranche onboarding (the AMFI next-250 landed with zero history) and every
    future IPO graduate — no one-shot env flags. No-op when nothing is
    missing (one aggregate query) or when Dhan is unconfigured."""
    try:
        from app.dhan import client as _dhan
        if not _dhan.configured():
            return
        from sqlalchemy import func as _f
        from app.database import SessionLocal
        from app import models
        from app.dhan.backfill import backfill_prices, sync_snapshots_from_history
        s = SessionLocal()
        try:
            counts = dict(s.query(models.HistoricalPrice.company_id,
                                  _f.count(models.HistoricalPrice.id))
                          .group_by(models.HistoricalPrice.company_id).all())
            missing = [c.ticker for c in s.query(models.Company).all()
                       if c.ticker and counts.get(c.id, 0) < 50]
            if not missing:
                return
            log.info(f"History self-heal: {len(missing)} names lack a price series — "
                     f"seeding 5y from Dhan…")
            log.info(f"History self-heal backfill: {backfill_prices(s, missing, years=5)}")
            log.info(f"History self-heal sync: {sync_snapshots_from_history(s, missing)}")
        finally:
            s.close()
    except Exception as e:
        log.error(f"History self-heal failed: {type(e).__name__}: {e}")


# Self-heal check daily, after the EOD top-up settles (4:15pm IST = 10:45 UTC)
for _day in ("monday", "tuesday", "wednesday", "thursday", "friday"):
    getattr(schedule.every(), _day).at("10:45").do(run_missing_history_backfill)


def run_macro_refresh():
    """Weekly macro-store refresh from the key-gated API sources (TE/MoSPI).
    No-op until the owner sets the keys on Railway; the RBI DBIE seed carries
    the engine either way."""
    try:
        from app.database import SessionLocal
        from app.macro_sources import refresh_all
        s = SessionLocal()
        try:
            log.info(f"Macro refresh: {refresh_all(s)}")
        finally:
            s.close()
    except Exception as e:
        log.error(f"Macro refresh failed: {type(e).__name__}: {e}")

# Macro sources — Mondays 05:00 IST (Sun 23:30 UTC), before the week opens
schedule.every().sunday.at("23:30").do(run_macro_refresh)


def run_regulatory_refresh():
    """Daily pull of the RBI + SEBI RSS feeds (official, public) into the
    regulatory tracker on the Economy page. Fail-silent."""
    try:
        from app.database import SessionLocal
        from app.regulatory import refresh
        from app.macro_sources import fetch_oecd_cli
        s = SessionLocal()
        try:
            log.info(f"Regulatory feed: {refresh(s)}")   # RBI+SEBI+PIB, extracts GST
            log.info(f"OECD CLI: {fetch_oecd_cli(s)} points")   # keyless, free
        finally:
            s.close()
    except Exception as e:
        log.error(f"Regulatory refresh failed: {type(e).__name__}: {e}")

# Regulatory + free macro feeds — daily 07:30 IST (02:00 UTC), before market open
schedule.every().day.at("02:00").do(run_regulatory_refresh)


def run_nse_flows():
    """Daily NSE feeds: FII/DII cash-market flows (→ macro store), insider
    (SEBI PIT) trades, promoter pledge %, and bulk/block deals for the visible
    universe (→ governance + flow signals)."""
    try:
        from app.database import SessionLocal
        from app.nse_sources import (fetch_fii_dii, fetch_insider_trades,
                                     fetch_pledge, fetch_deals)
        from app.ingest.indianapi_ingester import VISIBLE_UNIVERSE
        uni = list(VISIBLE_UNIVERSE)
        s = SessionLocal()
        try:
            log.info(f"NSE FII/DII: {fetch_fii_dii(s)} points")
            log.info(f"NSE deals: {fetch_deals(s)} names")
            log.info(f"NSE insider: {fetch_insider_trades(s, uni)} names")
            log.info(f"NSE pledge: {fetch_pledge(s, uni)} names")
        finally:
            s.close()
    except Exception as e:
        log.error(f"NSE flows failed: {type(e).__name__}: {e}")

# NSE flows — daily 08:00 IST (02:30 UTC)
schedule.every().day.at("02:30").do(run_nse_flows)


def run_transcript_ingest():
    """Daily concall-transcript ingestion over a bounded slice of the book, so
    the whole universe's key points refresh within a week. LLM narrative on
    only when ANTHROPIC_API_KEY is set; the rule-based extractor always runs."""
    try:
        import os
        from app.database import SessionLocal
        from app.transcript_ingester import ingest_universe
        s = SessionLocal()
        try:
            with_llm = bool(os.getenv("ANTHROPIC_API_KEY", "").strip()) and \
                os.getenv("TRANSCRIPT_LLM", "false").strip().lower() in ("1", "true", "yes")
            log.info(f"Transcript ingest: {ingest_universe(s, limit=150, with_llm=with_llm)}")
        finally:
            s.close()
    except Exception as e:
        log.error(f"Transcript ingest failed: {type(e).__name__}: {e}")

# Transcript ingestion — daily 06:30 IST (01:00 UTC), off-peak
schedule.every().day.at("01:00").do(run_transcript_ingest)

# Weekly full refresh — 6:00am IST Sunday = 00:30 UTC
schedule.every().sunday.at("00:30").do(run_full)

# Results calendar (board-meeting dates) — Saturday 04:00 IST = Fri 22:30 UTC
schedule.every().friday.at("22:30").do(run_results_calendar)

# Universe refresh (IPO graduates + index-rebalance entrants) — monthly, on
# the first Saturday 03:00 IST (= Fri 21:30 UTC).
def _monthly_universe_refresh():
    import datetime as _dt
    if _dt.date.today().day <= 7:
        run_universe_refresh()
schedule.every().friday.at("21:30").do(_monthly_universe_refresh)

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
                # Mark snapshots to the fresh closes too — history without the
                # snapshot sync left wider-tier names priced 9 days stale.
                from app.dhan.backfill import sync_snapshots_from_history as _sync_hist
                log.info(f"Snapshot sync: {_sync_hist(s, _tickers)}")
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
elif _flag("RUN_PROFILE_SNAPSHOTS"):
    # One-off / monthly: persist a Business-tab profile snapshot for every
    # visible name that lacks a fresh one, so profile pages survive vendor
    # quota exhaustion (July 2026: 10k/month burned, every profile empty).
    # Budget-guarded per name (~5 calls each); stops cleanly at the ceiling.
    log.info("RUN_PROFILE_SNAPSHOTS set — persisting company profile snapshots…")
    try:
        import time as _t
        from app.database import SessionLocal
        from app import models, api_budget
        from app.ingest.indianapi_ingester import VISIBLE_UNIVERSE
        from app.profile_routes import company_profile, _load_snapshot, _SNAP_TTL_DAYS
        s = SessionLocal()
        done = skipped = 0
        try:
            # One probe before the loop: if the vendor is refusing (monthly
            # quota 429), a 500-name pass is pure waste — abort with a log.
            from app.profile_routes import _get as _papi
            vendor_ok = _papi("/stock", {"name": "RELIANCE"}, ttl=60) is not None
            if not vendor_ok:
                log.info("Profile snapshots: vendor refusing calls (quota/down) — skipping; re-run after the monthly reset.")
            for tk in sorted(VISIBLE_UNIVERSE) if vendor_ok else []:
                co = s.query(models.Company).filter_by(ticker=tk).first()
                if co is None:
                    continue
                ins = s.query(models.CompanyInsight).filter_by(company_id=co.id).first()
                snap = _load_snapshot(ins)
                if snap and _t.time() - (snap.get("fetched_at") or 0) < _SNAP_TTL_DAYS * 86400:
                    skipped += 1
                    continue
                if api_budget.would_exceed(s, 5):
                    log.info(f"Profile snapshots: budget ceiling at {done} done — stopping.")
                    break
                try:
                    company_profile(tk, db=s)   # route handler persists the snapshot
                    done += 1
                except Exception as e:
                    log.warning(f"  {tk}: {type(e).__name__}: {e}")
                _t.sleep(1.0)                   # stay polite to the vendor
        finally:
            s.close()
        log.info(f"Profile snapshots done: {done} fetched, {skipped} already fresh. "
                 "Remove RUN_PROFILE_SNAPSHOTS now.")
    except Exception as e:
        log.error(f"Profile snapshots failed: {type(e).__name__}: {e}")
elif _flag("RUN_UNIVERSE_REFRESH"):
    log.info("RUN_UNIVERSE_REFRESH set — checking for new entrants…")
    run_universe_refresh()
    log.info("Done. Remove RUN_UNIVERSE_REFRESH from Variables now.")
elif _flag("RUN_RESULTS_CALENDAR"):
    log.info("RUN_RESULTS_CALENDAR set — sweeping board-meeting dates…")
    run_results_calendar()
    log.info("Done. Remove RUN_RESULTS_CALENDAR from Variables now.")
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

    # Seed missing price series right away (not just at the daily slot) so a
    # freshly onboarded tranche is fully usable the moment the deploy lands.
    run_missing_history_backfill()

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

    # FM v4 bootstrap: build the evidence/calibration artifacts when absent so
    # the Fund Manager triangulates from the first request after a deploy —
    # afterwards the nightly/monthly jobs keep them fresh.
    try:
        from app.database import SessionLocal as _SL
        from app import models as _mm
        from app.manager_engine import EVIDENCE_KEY, CALIBRATION_KEY, ENGINE_SCHEMA
        _s = _SL()
        try:
            have_ev = _s.query(_mm.KVStore).filter_by(key=EVIDENCE_KEY).first()
            have_cal = _s.query(_mm.KVStore).filter_by(key=CALIBRATION_KEY).first()
            ev_schema = ((have_ev.value or {}).get("schema") if have_ev else None)
        finally:
            _s.close()
        if not have_cal:
            log.info("FM v4 boot — no calibration artifact; running the first calibration…")
            run_manager_calibration()
        if not have_ev or ev_schema != ENGINE_SCHEMA:
            log.info(f"FM v4 boot — evidence missing or schema {ev_schema} != "
                     f"{ENGINE_SCHEMA}; rebuilding…")
            run_manager_evidence()
    except Exception as e:
        log.error(f"FM v4 bootstrap failed: {type(e).__name__}: {e}")

    # Populate the free keyless feeds (PIB regulatory + GST extract, OECD CLI)
    # right after a deploy so they're live now, not at the next daily slot.
    run_regulatory_refresh()

while True:
    schedule.run_pending()
    time.sleep(60)
