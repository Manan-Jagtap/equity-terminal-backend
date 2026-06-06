"""
purge_stale.py — ONE-TIME cleanup of HistoricalFinancial.

Two passes, both fast single DELETEs (no API calls, no re-ingestion):
  1. Drop any row NOT sourced from IndianAPI (leftover yfinance / bse_pdf).
  2. Drop any row whose line_item the CURRENT ingester no longer produces
     (orphans like nii / total_debt / total_income / employee_cost that an
     older ingester version wrote with source="indianapi" and that upserts
     never cleared). These were inflating year counts and feeding the Ratios
     tab stale numbers.

Run once:
  railway run python3 -m app.ingest.purge_stale
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from sqlalchemy import func
from app.database import SessionLocal
from app import models

# Exactly what indianapi_ingester._parse_financials + _insurer_statements write.
CANONICAL = {
    # P&L
    "revenue", "raw_material", "gross_profit", "ebit", "ebitda",
    "depreciation", "pbt", "tax", "pat", "interest_expense",
    # Balance sheet
    "net_worth", "equity", "reserves", "total_assets", "borrowings",
    "lt_debt", "cash", "total_liabilities", "fixed_assets", "investments",
    # Cash flow
    "operating_cf", "investing_cf", "financing_cf", "capex",
    "dividends", "net_change_cash", "fcf",
}


def run():
    s = SessionLocal()
    try:
        print("Sources before:",
              dict(s.query(models.HistoricalFinancial.source, func.count())
                    .group_by(models.HistoricalFinancial.source).all()))

        # Pass 1 — non-IndianAPI source
        n1 = (s.query(models.HistoricalFinancial)
                .filter(models.HistoricalFinancial.source != "indianapi")
                .delete(synchronize_session=False))

        # Pass 2 — orphan line items the current ingester doesn't produce
        stale_items = [r[0] for r in
                       s.query(models.HistoricalFinancial.line_item).distinct().all()
                       if r[0] not in CANONICAL]
        n2 = 0
        if stale_items:
            n2 = (s.query(models.HistoricalFinancial)
                    .filter(models.HistoricalFinancial.line_item.in_(stale_items))
                    .delete(synchronize_session=False))
        s.commit()

        print(f"\n✓ Pass 1 (non-IndianAPI source): deleted {n1}")
        print(f"✓ Pass 2 (orphan line items): deleted {n2}")
        if stale_items:
            print(f"  removed line items: {sorted(stale_items)}")
        print("Sources after:",
              dict(s.query(models.HistoricalFinancial.source, func.count())
                    .group_by(models.HistoricalFinancial.source).all()))
        # Sanity: show the line items that remain
        remaining = sorted(r[0] for r in
                           s.query(models.HistoricalFinancial.line_item).distinct().all())
        print("Line items remaining:", remaining)
    finally:
        s.close()


if __name__ == "__main__":
    run()
