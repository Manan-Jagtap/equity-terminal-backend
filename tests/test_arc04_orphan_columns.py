"""ARC-04 — the raw-ALTER orphan columns now come from the Alembic chain.

Before this, users.token_version (SEC-01) existed in NO revision: a database
built purely by `alembic upgrade head` (CI's Postgres job, a fresh container)
came up without it and only `_additive_migrations()` — raw ALTERs behind
`except: pass` in app/main.py — rescued auth. These tests build the schema the
way the entrypoint does (alembic ONLY — no create_all, no app.main import) and
prove the columns are there, that the revision is reversible for what it owns,
idempotent where prod already has the column, and that the raw ALTERs are gone."""
import os
import sqlite3

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _cfg():
    from alembic.config import Config
    cfg = Config(os.path.join(ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(ROOT, "alembic"))
    return cfg


def _cols(path, table):
    con = sqlite3.connect(path)
    try:
        return {r[1]: r for r in con.execute(f"pragma table_info({table})")}
    finally:
        con.close()


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """A brand-new sqlite file that alembic/env.py will target (it reads
    DATABASE_URL at every command), independent of the suite's app engine."""
    path = str(tmp_path / "alembic_only.db")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    return path


def test_alembic_head_is_arc04():
    from alembic.script import ScriptDirectory
    heads = list(ScriptDirectory.from_config(_cfg()).get_heads())
    assert heads == ["arc04orphans"], heads


def test_alembic_only_schema_carries_both_orphan_columns(fresh_db):
    from alembic import command
    command.upgrade(_cfg(), "head")
    users = _cols(fresh_db, "users")
    assert "token_version" in users, "users.token_version missing from an alembic-only schema"
    # matches app/models.py: NOT NULL with a server default of 0
    assert users["token_version"][3] == 1                     # notnull
    assert str(users["token_version"][4]).strip("'") == "0"   # dflt_value
    assert "buy_date" in _cols(fresh_db, "portfolio_holdings")
    # a user row inserted without token_version reads 0 (the auth contract:
    # a token minted with tv=0 must match a fresh account)
    con = sqlite3.connect(fresh_db)
    con.execute("insert into users(email, password_hash) values ('a@b.c', 'x')")
    assert con.execute("select token_version from users").fetchone() == (0,)
    con.close()


def test_downgrade_drops_only_what_the_revision_owns(fresh_db):
    from alembic import command
    command.upgrade(_cfg(), "head")
    command.downgrade(_cfg(), "-1")
    assert "token_version" not in _cols(fresh_db, "users")
    # buy_date is baseline schema — a downgrade to inst0102tel must keep it
    assert "buy_date" in _cols(fresh_db, "portfolio_holdings")
    command.upgrade(_cfg(), "head")
    assert "token_version" in _cols(fresh_db, "users")


def test_upgrade_is_a_noop_where_prod_already_has_the_column(fresh_db):
    """Prod got token_version from the raw ALTER (nullable INTEGER DEFAULT 0)
    on 19 Jul; the revision must skip it, not fail on a duplicate column."""
    from alembic import command
    command.upgrade(_cfg(), "inst0102tel")
    con = sqlite3.connect(fresh_db)
    con.execute("ALTER TABLE users ADD COLUMN token_version INTEGER DEFAULT 0")
    con.commit(); con.close()
    command.upgrade(_cfg(), "head")
    users = _cols(fresh_db, "users")
    assert "token_version" in users and users["token_version"][3] == 0  # left as prod has it
    con = sqlite3.connect(fresh_db)
    assert con.execute("select version_num from alembic_version").fetchone() == ("arc04orphans",)
    con.close()


def test_boot_no_longer_runs_raw_alters():
    """The third mechanism is retired: no ALTER string literal and no
    `_additive_migrations` definition may come back into the boot path."""
    with open(os.path.join(ROOT, "app", "main.py")) as f:
        src = f.read()
    assert "def _additive_migrations" not in src
    assert '"ALTER TABLE' not in src and "'ALTER TABLE" not in src
