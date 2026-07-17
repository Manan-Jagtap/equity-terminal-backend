"""
app/migrations_boot.py — apply Alembic migrations at web-process boot.

Replaces schema-by-convention (create_all + hardcoded ALTERs) with ordered,
reversible migrations, without a risky cutover:

  · DB has app tables but NO alembic_version  → STAMP head (adopt the existing
    prod/dev database as already-at-baseline; zero schema changes performed).
  · DB has alembic_version                    → UPGRADE head (apply anything new).
  · Empty DB                                  → UPGRADE head (build full schema
    from the baseline migration).

Deliberately fail-open: a migration error is logged LOUDLY but does not crash
the app — create_all still runs after this as a belt-and-braces for additive
changes, and a crash-looping web process is the worse failure mode for a
single-operator deployment (the uptime workflow surfaces the error instead).

Workflow for future schema changes: edit app/models.py →
`alembic revision --autogenerate -m "..."` → review the generated file →
commit; the next deploy applies it here.
"""
from __future__ import annotations
import logging
import os

log = logging.getLogger("app.migrations")


def run_boot_migrations() -> str:
    """Returns one of: 'stamped', 'upgraded', 'error:<msg>'."""
    try:
        from alembic import command
        from alembic.config import Config
        from sqlalchemy import inspect
        from app.database import engine

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cfg = Config(os.path.join(root, "alembic.ini"))
        cfg.set_main_option("script_location", os.path.join(root, "alembic"))

        insp = inspect(engine)
        tables = set(insp.get_table_names())
        has_version = "alembic_version" in tables
        has_app_tables = bool(tables - {"alembic_version"})

        if has_app_tables and not has_version:
            command.stamp(cfg, "head")
            log.info("migrations: existing schema adopted — stamped head")
            return "stamped"
        command.upgrade(cfg, "head")
        log.info("migrations: upgraded to head")
        return "upgraded"
    except Exception as e:
        log.error(f"migrations: FAILED ({e}) — continuing with create_all fallback")
        return f"error:{str(e)[:120]}"
