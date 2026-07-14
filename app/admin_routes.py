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
    last_ip = {}
    for ev in (db.query(models.AuthEvent)
                 .filter(models.AuthEvent.user_id.isnot(None))
                 .order_by(models.AuthEvent.created_at).all()):
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
