"""
scripts/add_bse_columns.py — Idempotent migration for BSE document storage.

Adds:
    1. companies.bse_scrip_code  (nullable, indexed)
    2. quarterly_documents       (new table)

Re-running is a no-op. Supports --dry-run for safety.

Usage:
    python -m scripts.add_bse_columns               # write
    python -m scripts.add_bse_columns --dry-run     # show what would happen
    DATABASE_URL="postgresql://..." python -m scripts.add_bse_columns

Run order:
    1. Edit app/models.py to declare the column + table (see README)
    2. Run this script
"""
from __future__ import annotations
import argparse
import os
import sys
from sqlalchemy import create_engine, inspect, text

from app.database import Base   # registers all models


def get_engine():
    url = os.environ.get("DATABASE_URL")
    if not url:
        # Default to local SQLite for testing
        url = "sqlite:///./equity_terminal.db"
        print(f"⚠ DATABASE_URL not set; using {url}")
    return create_engine(url)


def column_exists(insp, table: str, col: str) -> bool:
    if table not in insp.get_table_names():
        return False
    return col in {c["name"] for c in insp.get_columns(table)}


def table_exists(insp, table: str) -> bool:
    return table in insp.get_table_names()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Print actions without executing")
    args = ap.parse_args()

    engine = get_engine()
    insp = inspect(engine)
    dialect = engine.dialect.name
    print(f"DB dialect: {dialect}")
    print(f"DB URL: {engine.url.render_as_string(hide_password=True)}")
    print()

    actions: list[tuple[str, str]] = []

    # 1. companies.bse_scrip_code
    if column_exists(insp, "companies", "bse_scrip_code"):
        print("✓ companies.bse_scrip_code already exists — skipping")
    else:
        actions.append((
            "ALTER companies ADD bse_scrip_code",
            "ALTER TABLE companies ADD COLUMN bse_scrip_code VARCHAR(20)"
        ))
        # Index (Postgres + SQLite both support this syntax)
        actions.append((
            "CREATE INDEX ix_companies_bse_scrip_code",
            "CREATE INDEX IF NOT EXISTS ix_companies_bse_scrip_code "
            "ON companies(bse_scrip_code)"
        ))

    # 2. quarterly_documents table
    if table_exists(insp, "quarterly_documents"):
        print("✓ quarterly_documents table already exists — skipping")
    else:
        if dialect == "postgresql":
            create_sql = """
            CREATE TABLE quarterly_documents (
                id SERIAL PRIMARY KEY,
                company_id INTEGER NOT NULL
                    REFERENCES companies(id) ON DELETE CASCADE,
                quarter VARCHAR(10) NOT NULL,
                doc_type VARCHAR(20) NOT NULL,
                bse_announcement_id VARCHAR(100),
                bse_filing_date TIMESTAMP,
                source_url TEXT,
                r2_key TEXT NOT NULL,
                file_size_bytes INTEGER,
                extracted_text TEXT,
                char_count INTEGER,
                fetched_at TIMESTAMP DEFAULT NOW(),
                CONSTRAINT uq_qd_company_quarter_type
                    UNIQUE (company_id, quarter, doc_type)
            )
            """
        else:
            # SQLite
            create_sql = """
            CREATE TABLE quarterly_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL
                    REFERENCES companies(id) ON DELETE CASCADE,
                quarter VARCHAR(10) NOT NULL,
                doc_type VARCHAR(20) NOT NULL,
                bse_announcement_id VARCHAR(100),
                bse_filing_date DATETIME,
                source_url TEXT,
                r2_key TEXT NOT NULL,
                file_size_bytes INTEGER,
                extracted_text TEXT,
                char_count INTEGER,
                fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (company_id, quarter, doc_type)
            )
            """
        actions.append(("CREATE TABLE quarterly_documents", create_sql))
        actions.append((
            "CREATE INDEX ix_qd_company_quarter",
            "CREATE INDEX IF NOT EXISTS ix_qd_company_quarter "
            "ON quarterly_documents(company_id, quarter)"
        ))

    if not actions:
        print("\nNo actions needed. Schema is up to date.")
        return

    print(f"\n{len(actions)} action(s):")
    for label, sql in actions:
        print(f"  • {label}")

    if args.dry_run:
        print("\n--dry-run set, not executing.")
        return

    with engine.begin() as conn:
        for label, sql in actions:
            print(f"executing: {label}")
            conn.execute(text(sql))

    print("\n✓ Migration complete.")


if __name__ == "__main__":
    main()
