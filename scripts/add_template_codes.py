"""
scripts/add_template_codes.py

One-shot migration that:
  1. Adds a `template_code` column to the `companies` table (idempotent).
  2. Backfills the column for every existing company by running each row's
     sector string through app.templates.classify().
  3. Prints a summary so you can audit the classifications.

Re-runnable safely. Run again any time after `bulk_ingester` adds more
companies — only rows with NULL template_code are touched.

Usage:
    # Local (uses SQLite by default via DATABASE_URL fallback in database.py):
    python -m scripts.add_template_codes

    # Against Railway prod:
    DATABASE_URL=<your-railway-postgres-url> python -m scripts.add_template_codes

    # Dry run (classify but don't write):
    python -m scripts.add_template_codes --dry-run

Notes:
  - Safe to run on prod. The ALTER TABLE is idempotent. UPDATEs touch only
    rows where template_code IS NULL, so re-running won't clobber manual
    overrides you set later.
  - If you ever want to re-classify everything (e.g. after editing
    templates.py rules), pass --force.
"""
from __future__ import annotations
import sys
import argparse
from collections import Counter

from sqlalchemy import inspect, text
from app.database import engine, SessionLocal
from app import models
from app.templates import classify, ALL_TEMPLATES


def ensure_column_exists() -> bool:
    """Add companies.template_code if it doesn't exist. Returns True if added."""
    insp = inspect(engine)
    columns = {c["name"] for c in insp.get_columns("companies")}
    if "template_code" in columns:
        print("✓ Column companies.template_code already exists — skipping ALTER")
        return False

    dialect = engine.dialect.name  # 'postgresql' or 'sqlite'
    print(f"→ Adding column companies.template_code (dialect: {dialect})")

    with engine.begin() as conn:
        # Same syntax works on both Postgres ≥ 9.6 and SQLite ≥ 3.35.
        # Type: VARCHAR(20). Nullable so the migration can run before backfill.
        conn.execute(text(
            "ALTER TABLE companies ADD COLUMN template_code VARCHAR(20)"
        ))
    print("✓ Column added")
    return True


def backfill(dry_run: bool, force: bool) -> None:
    db = SessionLocal()
    try:
        # Pull every company. If force, re-classify all; else only NULL ones.
        q = db.query(models.Company)
        if not force:
            # Filter to where template_code IS NULL or empty.
            q = q.filter(
                (models.Company.template_code.is_(None))
                | (models.Company.template_code == "")
            )
        companies = q.all()

        if not companies:
            print("✓ No companies need backfilling — all rows already tagged")
            return

        print(f"\n→ Classifying {len(companies)} compan{'y' if len(companies)==1 else 'ies'}…\n")

        counts: Counter[str] = Counter()
        unrecognised: list[tuple[str, str]] = []

        for co in companies:
            template = classify(co.sector)
            counts[template] += 1

            # Flag companies that fell through to the MANUFACTURING default
            # AND don't look industrial — possible mis-classification.
            sector_lower = (co.sector or "").lower()
            looks_industrial = any(kw in sector_lower for kw in (
                "auto", "metal", "chemical", "cement", "telecom", "real estate",
                "industrial", "machinery", "construction", "infrastructure",
                "mining", "steel", "paper", "textile", "logistic", "transport",
                "aviation", "rubber", "plastic", "diversified", "conglomerate",
                "fertili", "packaging", "shipping", "defence", "defense",
                "aerospace", "manufactur",
            ))
            if template == "MANUFACTURING" and not looks_industrial:
                unrecognised.append((co.ticker, co.sector or "<empty>"))

            print(f"  {co.ticker:<14s} {(co.sector or '<no sector>'):<35s} → {template}")
            if not dry_run:
                co.template_code = template

        if dry_run:
            print("\n[DRY RUN] No changes written.")
        else:
            db.commit()
            print(f"\n✓ Committed {len(companies)} row(s)")

        # Summary
        print("\nDistribution:")
        for tmpl in ALL_TEMPLATES:
            n = counts.get(tmpl, 0)
            bar = "█" * min(n, 40)
            print(f"  {tmpl:<14s} {n:>4d}  {bar}")

        if unrecognised:
            print("\n⚠ The following defaulted to MANUFACTURING but the sector")
            print("  string doesn't look obviously industrial. Audit these")
            print("  and add a rule to app/templates.py if needed:\n")
            for ticker, sector in unrecognised:
                print(f"    {ticker:<14s} sector={sector!r}")

    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="Classify but don't write to the database.")
    parser.add_argument("--force", action="store_true",
                        help="Re-classify all companies, even ones already tagged.")
    args = parser.parse_args()

    # 1. Migration
    ensure_column_exists()

    # 2. Backfill
    backfill(dry_run=args.dry_run, force=args.force)

    print("\nDone.")


if __name__ == "__main__":
    main()
