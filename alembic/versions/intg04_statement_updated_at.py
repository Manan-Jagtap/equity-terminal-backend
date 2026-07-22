"""INTG-04: statement freshness — updated_at on financial_facts + historical_financials

Adds a nullable `updated_at` timestamp to both statement tables so fundamentals
staleness is measurable at rest (prices/insights already carry timestamps).

Additive and non-destructive: NULLABLE with no server default, so existing rows
read NULL ("last refresh unknown") — honestly, rather than a migration timestamp
that would falsely read "just refreshed". The model's client-side
default/onupdate stamps real times on the next ingest. Idempotent + reversible.

Revision ID: intg04stmtts
Revises: b1f13c0nv1ct
Create Date: 2026-07-22
"""
from alembic import op
import sqlalchemy as sa


revision = "intg04stmtts"
down_revision = "b1f13c0nv1ct"
branch_labels = None
depends_on = None

_TABLES = ("financial_facts", "historical_financials")


def _has_col(bind, table, col) -> bool:
    return col in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    for t in _TABLES:
        if not _has_col(bind, t, "updated_at"):
            op.add_column(t, sa.Column("updated_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    for t in _TABLES:
        if _has_col(bind, t, "updated_at"):
            op.drop_column(t, "updated_at")
