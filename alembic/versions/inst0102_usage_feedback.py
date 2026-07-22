"""INST-01/02: usage_events + feedback tables (self-owned, DPDP-fit instrumentation)

Two additive tables: usage_events (product analytics — event names only, no
IP/UA, 180-day retention enforced in the app) and feedback (the user-feedback
channel). user_id columns deliberately carry NO foreign key: DPDP account
erasure anonymises usage rows (null user_id) and deletes feedback rows, which an
FK cascade would either block or over-delete. Idempotent + reversible.

Revision ID: inst0102tel
Revises: intg04stmtts
Create Date: 2026-07-22
"""
from alembic import op
import sqlalchemy as sa


revision = "inst0102tel"
down_revision = "intg04stmtts"
branch_labels = None
depends_on = None


def _has_table(bind, name) -> bool:
    return sa.inspect(bind).has_table(name)


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "usage_events"):
        op.create_table(
            "usage_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), nullable=True, index=True),
            sa.Column("event", sa.String(64), nullable=False, index=True),
            sa.Column("detail", sa.String(200), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), index=True),
        )
    if not _has_table(bind, "feedback"):
        op.create_table(
            "feedback",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), nullable=True, index=True),
            sa.Column("message", sa.String(2000), nullable=False),
            sa.Column("page", sa.String(120), nullable=True),
            sa.Column("status", sa.String(16), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        )


def downgrade() -> None:
    bind = op.get_bind()
    for t in ("feedback", "usage_events"):
        if _has_table(bind, t):
            op.drop_table(t)
