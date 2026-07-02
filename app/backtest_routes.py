"""
app/backtest_routes.py — the model's verifiable track record.

  GET /api/backtest → cohort scorecard (BUY/ACCUMULATE/HOLD/REDUCE/AVOID),
                      the BUY−AVOID spread, and the full per-call ledger.

The ledger only starts when tracking started (no backfilled "history", no
survivorship editing) — that honesty is the whole point of the page.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.backtest import compute_backtest, take_snapshots

router = APIRouter(prefix="/api", tags=["backtest"])


@router.get("/backtest")
def backtest(db: Session = Depends(get_db)):
    try:
        return compute_backtest(db)
    except Exception:
        db.rollback()
        # Table may not exist before the first scheduler snapshot on a fresh
        # deploy — return an empty-but-valid shape instead of a 500.
        return {"as_of": None, "tracking_since": None, "snapshot_days": 0,
                "companies_tracked": 0, "total_calls": 0, "excluded_no_call": 0,
                "cohorts": {}, "buy_avoid_spread": None,
                "total_buy_avoid_spread": None, "benchmark": None, "calls": []}


@router.post("/backtest/snapshot")
def snapshot_now(db: Session = Depends(get_db)):
    """Manual snapshot trigger (idempotent per day). The scheduler does this
    automatically after every EOD price refresh and boot recompute."""
    from app import models
    from app.database import engine
    models.VerdictSnapshot.__table__.create(bind=engine, checkfirst=True)
    n = take_snapshots(db)
    return {"snapshotted": n}
