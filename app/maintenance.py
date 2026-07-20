"""One-off maintenance helpers — destructive, ops-invoked, never on a request
path. Kept generic (driven by table metadata) so they stay correct as the schema
grows."""
from __future__ import annotations

from . import models
from .database import Base


def delete_company_and_children(db, ticker: str) -> dict:
    """Fully remove a ticker: delete rows from EVERY table that carries a
    `company_id` FK (Postgres enforces the constraints, so orphaned children
    would block the parent delete), then the `companies` row itself. Iterates
    `Base.metadata.sorted_tables` in reverse FK-dependency order so children go
    before parents. Commits on success; returns a per-table delete count.

    Used to purge junk tickers — VAML/VISL are Vedanta-segment mis-resolutions
    that are not separately listed and were removed from the universe list, so
    the boot auto-create won't re-seed them. Idempotent: an already-absent
    ticker returns {}."""
    co = db.query(models.Company).filter_by(ticker=(ticker or "").upper()).first()
    if not co:
        return {}
    cid = co.id
    counts: dict[str, int] = {}
    for tbl in reversed(Base.metadata.sorted_tables):
        if tbl.name == "companies":
            continue
        if "company_id" in tbl.c:
            res = db.execute(tbl.delete().where(tbl.c.company_id == cid))
            counts[tbl.name] = res.rowcount or 0
    db.execute(models.Company.__table__.delete()
               .where(models.Company.__table__.c.id == cid))
    counts["companies"] = 1
    db.commit()
    return counts
