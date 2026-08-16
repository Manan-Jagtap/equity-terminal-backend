"""ARC-04: fold the raw-ALTER orphan columns into the migration chain

Until this revision the web boot ran THREE schema mechanisms: Alembic
(stamp-or-upgrade), Base.metadata.create_all, and `_additive_migrations()` in
app/main.py — two hardcoded `ALTER TABLE … ADD COLUMN` statements wrapped in
`except: pass`. Two columns lived only in that third mechanism:

  · users.token_version (SEC-01, 19 Jul) — added AFTER the 17-Jul baseline and
    never given a revision, so a database built purely by `alembic upgrade head`
    (CI's Postgres job, any fresh container) came up WITHOUT it and only the raw
    ALTER rescued auth. This revision is the real home for that column.
  · portfolio_holdings.buy_date (12 Jul) — already in the baseline for the
    fresh path; the raw ALTER only ever mattered for a database that was
    create_all'd from a pre-12-Jul model and then STAMPED. Guarded here so that
    edge keeps working once the raw ALTER is gone; a no-op everywhere else.

Prod (RDS) already has both — buy_date since the 18-Jul restore (create_all,
then stamped), token_version from the raw ALTER on the SEC-01 deploy — so the
inspector guard makes this a pure no-op there (the only code that runs on prod
is the same sa.inspect().get_columns() call intg04stmtts already ran on RDS).
token_version is declared as the model has it (NOT NULL, server default 0):
sqlite and postgres both accept ADD COLUMN with NOT NULL + DEFAULT and backfill
existing rows. Reversible for what this revision OWNS: downgrade drops
token_version only — buy_date belongs to the baseline and must survive a
downgrade to inst0102tel.

Revision ID: arc04orphans
Revises: inst0102tel
Create Date: 2026-08-16
"""
from alembic import op
import sqlalchemy as sa


revision = "arc04orphans"
down_revision = "inst0102tel"
branch_labels = None
depends_on = None

# (table, column) — column objects match app/models.py exactly.
_COLS = [
    ("users", sa.Column("token_version", sa.Integer(), server_default="0", nullable=False)),
    ("portfolio_holdings", sa.Column("buy_date", sa.Date(), nullable=True)),
]


def _has_col(bind, table, col) -> bool:
    return col in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    for table, column in _COLS:
        if not _has_col(bind, table, column.name):
            op.add_column(table, column)


def downgrade() -> None:
    bind = op.get_bind()
    # Only token_version is this revision's own — buy_date is baseline schema.
    if _has_col(bind, "users", "token_version"):
        op.drop_column("users", "token_version")
