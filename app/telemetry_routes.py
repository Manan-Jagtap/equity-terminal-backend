"""
telemetry_routes.py — INST-01/02: self-owned product analytics + the user-feedback
channel. DPDP posture throughout (same doctrine as error_log): OUR database only
(India-resident RDS), no third-party processor, data-minimal — an event name and
a short query-string-stripped detail; NO IP, NO user-agent. The owner finally
sees usage, and problems stop surfacing only as silent churn.
"""
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import models
from app.auth import get_current_user, get_optional_user
from app.database import get_db

router = APIRouter(prefix="/api", tags=["telemetry"])

# Event names are app-defined slugs ("view:screener", "export:onepager") — anything
# else is coerced, so the FE can never turn this into a free-text store.
_EVENT_RE = re.compile(r"[^a-z0-9:_\-.]")
RETENTION_DAYS = 180        # DPDP data-minimisation: usage older than this is pruned


def _clean_event(s: str) -> str:
    return _EVENT_RE.sub("", (s or "").lower())[:64]


def _clean_detail(s) -> str | None:
    if not s:
        return None
    return str(s).split("?", 1)[0][:200]    # never store query strings


class TelemetryIn(BaseModel):
    event: str
    detail: str | None = None


@router.post("/telemetry")
def record_event(body: TelemetryIn,
                 user: models.User | None = Depends(get_optional_user),
                 db: Session = Depends(get_db)):
    """Record one product event. Anonymous is fine (user_id null); logged-in
    events carry the user id so DAU is measurable. Fail-silent: telemetry must
    never break the app it observes."""
    ev = _clean_event(body.event)
    if not ev:
        return {"ok": False}
    try:
        db.add(models.UsageEvent(user_id=(user.id if user else None),
                                 event=ev, detail=_clean_detail(body.detail)))
        db.commit()
    except Exception:
        db.rollback()
    return {"ok": True}


class FeedbackIn(BaseModel):
    message: str
    page: str | None = None


@router.post("/feedback")
def submit_feedback(body: FeedbackIn,
                    user: models.User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """INST-02: signed-in users can tell the owner what's wrong (or right).
    Auth required — an anonymous free-text endpoint is a spam sink."""
    msg = (body.message or "").strip()
    if not msg:
        raise HTTPException(400, "Feedback message is empty")
    db.add(models.Feedback(user_id=user.id, message=msg[:2000],
                           page=_clean_detail(body.page)))
    db.commit()
    return {"ok": True, "received": True}


def prune_usage_events(db: Session, days: int = RETENTION_DAYS) -> int:
    """DPDP data-minimisation: drop usage events older than the retention window.
    Called opportunistically from the admin usage view (self-maintaining without
    a scheduler hook)."""
    import datetime as dt
    cutoff = dt.datetime.utcnow() - dt.timedelta(days=days)
    try:
        n = (db.query(models.UsageEvent)
               .filter(models.UsageEvent.created_at < cutoff)
               .delete(synchronize_session=False))
        db.commit()
        return n or 0
    except Exception:
        db.rollback()
        return 0
