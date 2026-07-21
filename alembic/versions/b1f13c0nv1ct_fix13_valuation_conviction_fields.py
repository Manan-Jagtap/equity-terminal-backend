"""FIX-13: conviction legs on the valuations table (the FM contract)

Adds five nullable columns consumed by FIX-19's Fund Manager:
data_tier, method_dispersion, sensitivity_swing, tv_share, gate_state.

All nullable → additive and non-destructive: existing rows keep every value and
read the new columns as NULL until the next scheduler recompute backfills them.
Fully reversible.

Revision ID: b1f13c0nv1ct
Revises: 974cc2004deb
Create Date: 2026-07-21
"""
from alembic import op
import sqlalchemy as sa


revision = "b1f13c0nv1ct"
down_revision = "974cc2004deb"
branch_labels = None
depends_on = None

_COLS = [
    ("data_tier", sa.String()),
    ("method_dispersion", sa.Float()),
    ("sensitivity_swing", sa.Float()),
    ("tv_share", sa.Float()),
    ("gate_state", sa.String()),
]


def _existing(bind) -> set[str]:
    insp = sa.inspect(bind)
    return {c["name"] for c in insp.get_columns("valuations")}


def upgrade() -> None:
    # Idempotent add — skip any column already present (safe re-run on a partially
    # migrated instance).
    have = _existing(op.get_bind())
    for name, type_ in _COLS:
        if name not in have:
            op.add_column("valuations", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    have = _existing(op.get_bind())
    for name, _ in reversed(_COLS):
        if name in have:
            op.drop_column("valuations", name)
