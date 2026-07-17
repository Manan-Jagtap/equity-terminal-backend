"""
app/admin_routes.py — owner-only visibility into who is signing up and coming
back. Gated by ADMIN_EMAILS (comma-separated, case-insensitive): the caller
must be authenticated AND their email must be on the list. 403 otherwise —
including when the env var is unset (fail closed, never open).

  GET /api/admin/users → [{id, email, name, created_at, last_login,
                           login_count, last_ip}] newest signup first
  GET /api/admin/auth-events?limit=100 → recent raw events (incl. failures)
"""
import os

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app import models
from app.auth import get_current_user

router = APIRouter(prefix="/api/admin", tags=["admin"])


def require_admin(user: models.User = Depends(get_current_user)) -> models.User:
    allowed = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()}
    if not allowed or (user.email or "").lower() not in allowed:
        raise HTTPException(403, "Admin access required")
    return user


@router.get("/users")
def list_users(user: models.User = Depends(require_admin),
               db: Session = Depends(get_db)):
    logins = dict(db.query(models.AuthEvent.user_id, func.count())
                    .filter(models.AuthEvent.event == "login")
                    .group_by(models.AuthEvent.user_id).all())
    last_seen = dict(db.query(models.AuthEvent.user_id, func.max(models.AuthEvent.created_at))
                       .filter(models.AuthEvent.event.in_(("login", "signup")))
                       .group_by(models.AuthEvent.user_id).all())
    # Last-seen IP per user WITHOUT loading the whole auth_events table (audit
    # D9): the row with the max created_at per user, via a correlated subquery.
    _sub = (db.query(models.AuthEvent.user_id.label("uid"),
                     func.max(models.AuthEvent.created_at).label("mx"))
              .filter(models.AuthEvent.user_id.isnot(None))
              .group_by(models.AuthEvent.user_id).subquery())
    last_ip = {}
    for ev in (db.query(models.AuthEvent)
                 .join(_sub, (models.AuthEvent.user_id == _sub.c.uid)
                       & (models.AuthEvent.created_at == _sub.c.mx)).all()):
        if ev.ip:
            last_ip[ev.user_id] = ev.ip
    users = db.query(models.User).order_by(models.User.created_at.desc()).all()
    return {"count": len(users), "users": [{
        "id": u.id, "email": u.email, "name": u.name,
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "last_login": last_seen[u.id].isoformat() if last_seen.get(u.id) else None,
        "login_count": logins.get(u.id, 0),
        "last_ip": last_ip.get(u.id),
    } for u in users]}


@router.get("/auth-events")
def list_auth_events(limit: int = 100,
                     user: models.User = Depends(require_admin),
                     db: Session = Depends(get_db)):
    rows = (db.query(models.AuthEvent)
              .order_by(models.AuthEvent.created_at.desc())
              .limit(max(1, min(limit, 500))).all())
    return {"count": len(rows), "events": [{
        "email": r.email, "event": r.event, "ip": r.ip,
        "user_agent": r.user_agent,
        "at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]}


@router.get("/coverage")
def coverage(db: Session = Depends(get_db), _admin: models.User = Depends(require_admin)):
    """Per-name, per-tab data-coverage matrix for the visible universe — which
    tab is empty for which company, and the summary counts. The fundamentals
    backfill targets exactly the names this flags."""
    from app.coverage import coverage_rows, summary
    from app.ingest.indianapi_ingester import VISIBLE_UNIVERSE
    rows = coverage_rows(db, VISIBLE_UNIVERSE)
    return {"summary": summary(rows),
            "gaps": [r for r in rows
                     if r["statements"] < 4 or not r["has_insight"]],
            "rows": rows}


@router.get("/dhan-totp")
def dhan_totp_check(_admin: models.User = Depends(require_admin)):
    """Owner-only TOTP sanity check: shows the code OUR server derives from
    DHAN_TOTP_SECRET right now, so the owner can hold it next to their
    authenticator app. Match → the stored secret is right (suspect PIN or an
    unfinished TOTP activation on web.dhan.co). Mismatch → re-copy the secret.
    Never returns the secret itself; a 30-second one-time code is worthless
    on its own."""
    import time
    from app.dhan import auth
    if not auth.enabled():
        return {"enabled": False,
                "message": "DHAN_CLIENT_ID / DHAN_PIN / DHAN_TOTP_SECRET not all set."}
    _, _, sec = auth._creds()
    now = time.time()
    return {
        "enabled": True,
        "server_code": auth.totp_now(sec),
        "seconds_left": int(30 - now % 30),
        "secret_len": len(sec),
        "how_to": "Open your authenticator's Dhan API entry NOW. If its code "
                  "differs from server_code, the secret saved on Railway is not "
                  "the one Dhan issued — re-copy it. If codes MATCH but token "
                  "minting still fails, the API TOTP setup on web.dhan.co was "
                  "not completed (it must be confirmed with a code) or the PIN "
                  "is wrong.",
    }


@router.get("/api-usage")
def api_usage(_admin: models.User = Depends(require_admin)):
    """The vendor's REAL IndianAPI quota (from /usage on the analyst host) —
    total_requests, hard_limit, remaining — plus our own budget tally. Lets the
    owner see actual consumption instead of the internal estimate."""
    import os, requests
    from app import api_budget
    from app.database import SessionLocal
    key = os.getenv("INDIANAPI_KEY", "").strip()
    base = os.getenv("INDIANAPI_ANALYST_BASE", "https://analyst.indianapi.in").rstrip("/")
    vendor = None
    try:
        r = requests.get(base + "/usage", headers={"x-api-key": key}, timeout=15)
        if r.status_code == 200:
            vendor = r.json()
    except Exception:
        vendor = None
    s = SessionLocal()
    try:
        internal = {"month": api_budget.current_month(),
                    "counted": api_budget.month_usage(s),
                    "budget": api_budget.budget()}
    finally:
        s.close()
    return {"vendor": vendor, "internal": internal}


@router.post("/fm-engine/rebuild")
def fm_engine_rebuild(calibrate: bool = False,
                      _admin: models.User = Depends(require_admin),
                      db: Session = Depends(get_db)):
    """Rebuild the Fund Manager v4 evidence (and optionally re-run the monthly
    signal calibration) on demand instead of waiting for the nightly job."""
    from app.manager_engine import snapshot_evidence
    out = {"evidence": snapshot_evidence(db)}
    if calibrate:
        from app.manager_calibration import run_calibration
        art = run_calibration(db)
        out["calibration"] = {"as_of": art.get("as_of"), "ic": art.get("ic"),
                              "weights": art.get("weights"),
                              "runtime_s": art.get("runtime_s")}
    return out


@router.get("/fm-engine")
def fm_engine_state(_admin: models.User = Depends(require_admin),
                    db: Session = Depends(get_db)):
    """Full engine state: calibration artifact + evidence/macro summaries."""
    from app import models as _m
    from app.manager_engine import CALIBRATION_KEY, load_evidence, load_macro
    cal = db.query(_m.KVStore).filter_by(key=CALIBRATION_KEY).first()
    ev = load_evidence(db) or {}
    return {"calibration": (cal.value if cal else None),
            "evidence": {"as_of": ev.get("as_of"),
                         "names": len(ev.get("names") or {}),
                         "weights": ev.get("weights"),
                         "model_trust": ev.get("model_trust")},
            "macro": load_macro(db)}


@router.get("/macro")
def macro_state(_admin: models.User = Depends(require_admin),
                db: Session = Depends(get_db)):
    """Macro store inventory: every series with freshness, plus the summary
    block the engine consumes and which API keys are live."""
    import os as _os
    from app.macro_data import catalog, macro_summary
    return {"summary": macro_summary(db),
            "series": catalog(db),
            "keys": {"tradingeconomics": bool(_os.getenv("TRADINGECONOMICS_KEY", "").strip()),
                     "mospi": bool(_os.getenv("MOSPI_KEY", "").strip())}}


@router.post("/macro/refresh")
def macro_refresh(_admin: models.User = Depends(require_admin),
                  db: Session = Depends(get_db)):
    """Pull the key-gated API sources now instead of waiting for Monday."""
    from app.macro_sources import refresh_all
    return refresh_all(db)


class SegmentRow(BaseModel):
    name: str
    kind: str | None = None            # "operating" (default) or "stake"
    ebit: float | None = None          # ₹cr segment result (for operating segments)
    value: float | None = None         # ₹cr stake market value (for listed-subsidiary segments)
    sector: str | None = None          # SECTOR key → picks the EV/EBITDA multiple
    revenue: float | None = None


class SegmentRefresh(BaseModel):
    ticker: str
    segments: list[SegmentRow]         # read straight off the filing's segment table
    net_debt: float | None = None      # ₹cr; parent net debt for the SOTP (default 0)
    shares: float | None = None        # ₹cr shares; defaults to the company's shares outstanding
    as_of: str | None = None


@router.post("/segment-refresh")
def segment_refresh(body: SegmentRefresh,
                    _admin: models.User = Depends(require_admin),
                    db: Session = Depends(get_db)):
    """Store REPORTED segment financials (Ind-AS 108 — read straight off the filing,
    no AI) and return the computed data-driven SOTP. The conglomerate then values on
    real segments (segment EBIT × sector multiple / listed-stake value) instead of
    the illustrative preset. Pass an empty `segments` list to clear a name back to
    the preset."""
    from app.segment_sotp import store_segments, compute_sotp, normalise_segments, delete_segments
    co = db.query(models.Company).filter_by(ticker=body.ticker.upper()).first()
    if co is None:
        raise HTTPException(404, f"Unknown ticker {body.ticker}")
    segs = normalise_segments([s.model_dump() for s in body.segments])
    if not segs:
        # The documented clear path: an empty list reverts the name to its
        # illustrative preset (previously this 422'd, contradicting the docstring).
        removed = delete_segments(db, body.ticker)
        return {"ticker": body.ticker.upper(), "cleared": removed, "segments": [], "sotp": None}
    nd = body.net_debt if body.net_debt is not None else 0.0
    sh = body.shares or co.shares_outstanding or 0.0
    store_segments(db, body.ticker, segs, as_of=body.as_of, net_debt=nd, shares=sh,
                   source="admin/segment-refresh")
    return {"ticker": body.ticker.upper(), "segments": segs,
            "sotp": compute_sotp(segs, nd, sh)}


@router.get("/errors")
def recent_error_log(_admin: models.User = Depends(require_admin),
                     db: Session = Depends(get_db)):
    """Self-owned error telemetry: the most recent unhandled exceptions (newest
    first) from the DB ring buffer — timestamp, path (no query string),
    exception class, truncated message. Never IPs/users/bodies."""
    from app.error_log import recent_errors, errors_last_hour
    return {"errors_1h": errors_last_hour(db), "recent": recent_errors(db)}


@router.get("/data-integrity")
def data_integrity(_admin: models.User = Depends(require_admin),
                   db: Session = Depends(get_db)):
    """Latest continuous data-integrity sweep (red/amber/green + findings).
    Runs weekly from the scheduler; POST /data-integrity/run for on-demand."""
    from app.data_integrity import load_sweep
    return load_sweep(db) or {"status": "never_run",
                              "note": "No sweep stored yet — POST /api/admin/data-integrity/run."}


@router.post("/data-integrity/run")
def data_integrity_run(_admin: models.User = Depends(require_admin),
                       db: Session = Depends(get_db)):
    """Run the integrity sweep now and persist it (read-only over the DB)."""
    from app.data_integrity import store_sweep
    return store_sweep(db)


@router.get("/segment-financials")
def segment_financials(_admin: models.User = Depends(require_admin),
                       db: Session = Depends(get_db)):
    """The verified-segment store, each name with its computed SOTP, plus which
    SOTP conglomerates still ride ILLUSTRATIVE presets (i.e. need a verified
    entry). Powers the in-app segment editor."""
    from app.segment_sotp import load_store, compute_sotp
    from app.alt_models import SOTP_PRESETS
    store = load_store(db)
    out = {}
    for tk, rec in store.items():
        out[tk] = {**rec, "sotp": compute_sotp(rec.get("segments") or [],
                                               rec.get("net_debt") or 0.0,
                                               rec.get("shares") or 0.0)}
    return {"store": out,
            "presets_only": sorted(t for t in SOTP_PRESETS if t not in store),
            "preset_tickers": sorted(SOTP_PRESETS.keys())}


@router.delete("/segment-financials/{ticker}")
def segment_financials_delete(ticker: str,
                              _admin: models.User = Depends(require_admin),
                              db: Session = Depends(get_db)):
    """Remove a name's verified segments — it reverts to the illustrative preset."""
    from app.segment_sotp import delete_segments
    return {"ticker": ticker.upper(), "cleared": delete_segments(db, ticker)}


@router.post("/macro/upload")
async def macro_upload(file: UploadFile = File(...),
                       _admin: models.User = Depends(require_admin),
                       db: Session = Depends(get_db)):
    """Re-upload a fresh RBI DBIE macro export (either workbook format the
    seed came from); every series merges into the overlay, newest-wins."""
    from io import BytesIO
    from app.macro_sources import ingest_dbie_xlsx
    blob = await file.read()
    if len(blob) > 10 * 1024 * 1024:
        raise HTTPException(413, "File too large (10 MB max)")
    try:
        return ingest_dbie_xlsx(db, BytesIO(blob))
    except Exception as e:
        raise HTTPException(400, f"Could not parse workbook: {type(e).__name__}: {e}")


@router.post("/ingest/transcripts")
def ingest_transcripts(limit: int = 40, llm: bool = False,
                       _admin: models.User = Depends(require_admin),
                       db: Session = Depends(get_db)):
    """On-demand concall-transcript ingestion (the nightly job otherwise)."""
    from app.transcript_ingester import ingest_universe
    return ingest_universe(db, limit=limit, with_llm=llm)


@router.post("/ingest/nse-flows")
def ingest_nse_flows(insider_limit: int = 60,
                     _admin: models.User = Depends(require_admin),
                     db: Session = Depends(get_db)):
    """On-demand NSE FII/DII + insider-trade refresh (the nightly job otherwise)."""
    from app.nse_sources import (fetch_fii_dii, fetch_insider_trades,
                                 fetch_pledge, fetch_deals)
    from app.ingest.indianapi_ingester import VISIBLE_UNIVERSE
    uni = list(VISIBLE_UNIVERSE)
    return {"fii_dii_points": fetch_fii_dii(db),
            "deals_names": fetch_deals(db),
            "insider_names": fetch_insider_trades(db, uni, limit=insider_limit),
            "pledge_names": fetch_pledge(db, uni, limit=insider_limit)}


class ActivityPoint(BaseModel):
    slug: str
    date: str      # ISO YYYY-MM-DD (month-end for monthly series)
    value: float
    name: str | None = None
    freq: str | None = "M"


@router.post("/macro/activity-point")
def macro_activity_point(body: ActivityPoint,
                         _admin: models.User = Depends(require_admin),
                         db: Session = Depends(get_db)):
    """Inject one macro/activity data point into the overlay — the reliable
    monthly path for indicators with no live API (GST collections, PMI, peak
    power, e-way bills, auto sales). Fills the 'awaiting feed' card instantly
    with a real, current, human-verified number. Idempotent per (slug, date)."""
    from app import macro_data
    name = body.name or (macro_data.ACTIVITY_META.get(body.slug, (body.slug,))[0])
    n = macro_data.write_overlay(db, {body.slug: {
        "name": name, "freq": body.freq or "M",
        "points": [[body.date[:10], float(body.value)]]}})
    return {"slug": body.slug, "written": n, "as_of": body.date[:10], "name": name}


# ── Valuation recompute ──────────────────────────────────────────────────────
# Rebuild the stored `valuations` cache with the CURRENT engine, on demand, so an
# engine change goes live immediately instead of waiting for the nightly job.
# Runs in a daemon thread (the full universe takes a couple of minutes and
# manages its own DB session), with a status endpoint to poll.
import threading as _threading
import datetime as _dt

_recompute_state = {"running": False, "scope": None, "started_at": None,
                    "finished_at": None, "error": None}
_recompute_lock = _threading.Lock()


def _recompute_job(scope: str):
    _recompute_state.update(running=True, scope=scope, error=None,
                            started_at=_dt.datetime.utcnow().isoformat() + "Z",
                            finished_at=None)
    try:
        from app.ingest.compute_valuations import run
        run(nifty50=(scope == "nifty50"), visible=(scope == "visible"))
    except Exception as e:  # noqa: BLE001 — record, never crash the worker
        _recompute_state["error"] = f"{type(e).__name__}: {e}"
    finally:
        _recompute_state.update(running=False,
                                finished_at=_dt.datetime.utcnow().isoformat() + "Z")


@router.post("/recompute-valuations")
def recompute_valuations(scope: str = "all",
                         _admin: models.User = Depends(require_admin)):
    """Trigger a full (scope=all) or scoped (nifty50 / visible) recompute of the
    stored valuations. Non-blocking; poll GET for progress. 409 if already running."""
    with _recompute_lock:
        if _recompute_state["running"]:
            raise HTTPException(409, "A recompute is already running")
        _threading.Thread(target=_recompute_job, args=(scope,), daemon=True).start()
    return {"status": "started", "scope": scope}


@router.get("/recompute-valuations")
def recompute_valuations_status(_admin: models.User = Depends(require_admin)):
    return dict(_recompute_state)


# ── One-click fundamentals + profile backfill ────────────────────────────────
# The browser-triggerable equivalent of the RUN_FUNDAMENTALS_BACKFILL +
# RUN_PROFILE_SNAPSHOTS scheduler flags — no Railway redeploy, no flag to remove.
# Budget-guarded (api_budget) and background-threaded, so it's safe to run anytime.
_backfill_state = {"running": False, "step": None, "started_at": None,
                   "finished_at": None, "error": None, "log": []}
_backfill_lock = _threading.Lock()


def _backfill_job(do_fundamentals: bool, do_profiles: bool):
    _backfill_state.update(running=True, step=None, error=None, log=[],
                           started_at=_dt.datetime.utcnow().isoformat() + "Z", finished_at=None)

    def _log(m):
        _backfill_state["log"].append(m)

    try:
        from app.database import SessionLocal
        from app.ingest.indianapi_ingester import run as indianapi_run, VISIBLE_UNIVERSE
        if do_fundamentals:
            _backfill_state["step"] = "fundamentals"
            from app.coverage import needs_fundamentals
            from app.ingest.compute_valuations import run as compute_valuations
            s = SessionLocal()
            try:
                targets = needs_fundamentals(s, VISIBLE_UNIVERSE)
            finally:
                s.close()
            _log(f"fundamentals: {len(targets)} names need a backfill")
            if targets:
                indianapi_run(tickers=targets, insights=True)
                compute_valuations()
                _log("fundamentals ingest + recompute complete")
        if do_profiles:
            _backfill_state["step"] = "profiles"
            import time as _t
            from app import api_budget
            from app.profile_routes import (company_profile, _load_snapshot,
                                            _SNAP_TTL_DAYS, _get as _papi)
            s = SessionLocal()
            done = skipped = 0
            try:
                vendor_ok = _papi("/stock", {"name": "RELIANCE"}, ttl=60) is not None
                if not vendor_ok:
                    _log("profiles: vendor refusing calls (quota/down) — skipped")
                for tk in (sorted(VISIBLE_UNIVERSE) if vendor_ok else []):
                    co = s.query(models.Company).filter_by(ticker=tk).first()
                    if co is None:
                        continue
                    ins = s.query(models.CompanyInsight).filter_by(company_id=co.id).first()
                    snap = _load_snapshot(ins)
                    if snap and _t.time() - (snap.get("fetched_at") or 0) < _SNAP_TTL_DAYS * 86400:
                        skipped += 1
                        continue
                    if api_budget.would_exceed(s, 5):
                        _log(f"profiles: budget ceiling at {done} done — stopping")
                        break
                    try:
                        company_profile(tk, db=s)
                        done += 1
                    except Exception as e:  # noqa: BLE001
                        _log(f"  {tk}: {type(e).__name__}")
                    _t.sleep(1.0)
            finally:
                s.close()
            _log(f"profiles: {done} fetched, {skipped} already fresh")
    except Exception as e:  # noqa: BLE001 — record, never crash the worker
        _backfill_state["error"] = f"{type(e).__name__}: {e}"
    finally:
        _backfill_state.update(running=False, step=None,
                               finished_at=_dt.datetime.utcnow().isoformat() + "Z")


@router.post("/run-backfill")
def run_backfill(fundamentals: bool = True, profiles: bool = True,
                 _admin: models.User = Depends(require_admin)):
    """One-click equivalent of RUN_FUNDAMENTALS_BACKFILL + RUN_PROFILE_SNAPSHOTS —
    runs both in a background thread, budget-guarded, no redeploy/flag needed.
    Poll GET for progress. 409 if already running."""
    with _backfill_lock:
        if _backfill_state["running"]:
            raise HTTPException(409, "A backfill is already running")
        _threading.Thread(target=_backfill_job, args=(fundamentals, profiles),
                          daemon=True).start()
    return {"status": "started", "fundamentals": fundamentals, "profiles": profiles}


@router.get("/run-backfill")
def run_backfill_status(_admin: models.User = Depends(require_admin)):
    return dict(_backfill_state)


@router.get("/coverage-gaps")
def coverage_gaps(_admin: models.User = Depends(require_admin),
                  db: Session = Depends(get_db)):
    """The un-ingested placeholder rows — auto-created names whose first /stock
    fundamentals fetch never completed (IndianAPI quota). Sector reads 'Unknown'
    and there are no HistoricalFinancial rows. Lists them so the owner can target
    a re-ingest and verify coverage afterwards."""
    have_stmts = {cid for (cid,) in db.query(models.HistoricalFinancial.company_id).distinct()}
    gaps = []
    for co in db.query(models.Company).order_by(models.Company.ticker).all():
        if (co.sector or "").lower() == "unknown" or co.id not in have_stmts:
            gaps.append({"ticker": co.ticker, "name": co.name, "sector": co.sector,
                         "shares": co.shares_outstanding,
                         "has_statements": co.id in have_stmts})
    return {"count": len(gaps), "tickers": [g["ticker"] for g in gaps], "rows": gaps}
