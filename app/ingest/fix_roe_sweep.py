"""
fix_roe_sweep.py — re-derive per-company ROE assumptions for already-seeded
financial companies whose Assumptions still carry the old fixed 0.155.

Idempotent and safe: only touches financials, only updates the four RIM fields,
commits per company. Run as many times as you like.

  python -m app.ingest.fix_roe_sweep            # only rows still at 0.155
  python -m app.ingest.fix_roe_sweep --all      # every financial, force refresh
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import yfinance as yf
from app.database import SessionLocal
from app import models


def derive_financial_assumptions(info):
    """Identical logic to bulk_ingester.derive_financial_assumptions."""
    roe = info.get("returnOnEquity")
    payout_ratio = info.get("payoutRatio")
    if roe and 0.02 < roe < 0.60:
        forecast_roe = max(0.08, min(roe, 0.30))
    else:
        forecast_roe = 0.150
    terminal_roe = max(0.10, min(round(0.40 * forecast_roe + 0.60 * 0.135, 4), 0.18))
    payout = payout_ratio if (payout_ratio and 0 <= payout_ratio < 0.95) else 0.20
    return dict(forecast_roe=round(forecast_roe, 4),
                terminal_roe=terminal_roe, payout=round(payout, 4))


def run(force_all=False):
    db = SessionLocal()
    try:
        fins = (db.query(models.Company)
                  .filter(models.Company.type == "financial").all())
        print(f"Found {len(fins)} financial companies.")
        updated = skipped = failed = 0
        for i, co in enumerate(fins, 1):
            a = co.assumptions
            if a is None:
                print(f"  [{i}/{len(fins)}] {co.ticker}: no assumptions row, skipping")
                skipped += 1
                continue
            # only touch rows still at the old fixed default unless --all
            is_stale = (a.forecast_roe is None or abs((a.forecast_roe or 0) - 0.155) < 1e-6)
            if not force_all and not is_stale:
                skipped += 1
                continue
            try:
                info = yf.Ticker(co.ticker + ".NS").info
                d = derive_financial_assumptions(info)
                a.forecast_roe = d["forecast_roe"]
                a.terminal_roe = d["terminal_roe"]
                a.payout = d["payout"]
                db.commit()
                print(f"  [{i}/{len(fins)}] {co.ticker}: forecast_roe -> {d['forecast_roe']:.3f}  "
                      f"terminal_roe -> {d['terminal_roe']:.3f}")
                updated += 1
                time.sleep(2)
            except Exception as e:
                db.rollback()
                print(f"  [{i}/{len(fins)}] {co.ticker}: ERROR — {e}")
                failed += 1
        print(f"\nUpdated: {updated}  Skipped: {skipped}  Failed: {failed}")
    finally:
        db.close()


if __name__ == "__main__":
    run(force_all="--all" in sys.argv)
