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
from app.admin_routes import require_admin
from app import models

from app.valuation_public import SUPPRESSING_GATES

router = APIRouter(prefix="/api", tags=["backtest"])


def _withhold_point_estimates(payload: dict, db: Session) -> dict:
    """Strip `intrinsic_at_call` / `mos_at_call` for names the engine currently
    withholds a fair value for — and NOTHING else.

    This endpoint is unauthenticated and was serving 119 rows across 98 tickers
    carrying the exact figures the rest of the product refuses to show, including
    ADANIGREEN at 64.36 while /api/companies/ADANIGREEN reports intrinsic: null.
    valuation_public.py's docstring names `backtest` as a caller that must ask
    the gate; it never did.

    THE LEDGER IS NOT TOUCHED. Every row survives, and so does everything the
    public track record actually consists of: the verdict, both dates, both
    prices, the return, the dividend leg, the win/loss. Only the two POINT
    ESTIMATES are withheld, which is the same presentation contract every other
    surface applies — the ledger records what we CALLED and how it did, not what
    we thought the share was worth.

    That is a judgement, and a reversible one: delete this function's call site
    and the figures come back. It is applied at the boundary, so the stored and
    computed ledger is unchanged either way.
    """
    calls = payload.get("calls")
    if not isinstance(calls, list) or not calls:
        return payload
    try:
        rows = db.query(models.Valuation.company_id, models.Valuation.gate_state).all()
        gated = {cid for cid, gate in rows if gate in SUPPRESSING_GATES}
        if not gated:
            return payload
        by_ticker = {c.id: c.ticker for c in db.query(models.Company.id, models.Company.ticker).all()}
        sup_tickers = {by_ticker.get(cid) for cid in gated}
    except Exception:
        db.rollback()
        return payload          # never let the guard take the endpoint down
    n = 0
    for row in calls:
        if row.get("ticker") in sup_tickers and (
                row.get("intrinsic_at_call") is not None or row.get("mos_at_call") is not None):
            row["intrinsic_at_call"] = None
            row["mos_at_call"] = None
            row["value_withheld"] = True
            n += 1
    if n:
        payload["note_value_withheld"] = (
            f"{n} call(s) omit the fair value and margin of safety recorded at the "
            "time: the engine currently withholds a point estimate for those names. "
            "The verdict, dates, prices and realised return are unchanged — the "
            "track record is what was called and how it did.")
    return payload


@router.get("/backtest")
def backtest(db: Session = Depends(get_db)):
    try:
        return _withhold_point_estimates(compute_backtest(db), db)
    except Exception:
        db.rollback()
        # Table may not exist before the first scheduler snapshot on a fresh
        # deploy — return an empty-but-valid shape instead of a 500.
        return {"as_of": None, "tracking_since": None, "snapshot_days": 0,
                "companies_tracked": 0, "total_calls": 0, "excluded_no_call": 0,
                "cohorts": {}, "buy_avoid_spread": None,
                "total_buy_avoid_spread": None, "benchmark": None, "calls": []}


@router.post("/backtest/snapshot")
def snapshot_now(db: Session = Depends(get_db),
                 _admin: models.User = Depends(require_admin)):
    """Manual snapshot trigger (idempotent per day). The scheduler does this
    automatically after every EOD price refresh and boot recompute. ADMIN-ONLY
    (it runs DDL + writes rows) — audit D5."""
    from app.database import engine
    models.VerdictSnapshot.__table__.create(bind=engine, checkfirst=True)
    n = take_snapshots(db)
    return {"snapshotted": n}
