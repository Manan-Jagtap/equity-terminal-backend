"""DATA-01 follow-up — VAML/VISL are removed from the ticker universe.

They are not separately-listed NSE tickers (the vendor resolved both to one
micro Vedanta-segment entity and stored byte-identical clones). Dropped from the
universe so the boot auto-create never re-seeds them. Existing DB rows are
deleted as a one-off ops step; this locks the code side so they can't return."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_junk.db")

from app.ingest import indianapi_ingester as ing


def test_vaml_visl_gone_from_every_tier_and_visible_universe():
    everywhere = set()
    for tier in ing._TIERS.values():
        everywhere |= set(tier)
    everywhere |= set(ing.VISIBLE_UNIVERSE)
    assert "VAML" not in everywhere
    assert "VISL" not in everywhere
    for junk in ("BOSCH-HCIL", "MARINE", "WHEELS"):
        assert junk not in everywhere


def _mk_db():
    from app.database import Base, engine, SessionLocal
    Base.metadata.create_all(engine)
    return SessionLocal(), engine, Base


def test_delete_company_and_children_is_complete_and_idempotent():
    from app.database import Base, engine, SessionLocal
    from app import models
    from app.maintenance import delete_company_and_children
    Base.metadata.create_all(engine)
    s = SessionLocal()
    try:
        co = models.Company(ticker="JUNKX", name="Junk", type="nonfinancial",
                            sector="X", shares_outstanding=1.0)
        s.add(co); s.commit()
        cid = co.id
        s.add(models.HistoricalFinancial(company_id=cid, fiscal_year=2024,
              statement_type="PL", line_item="revenue", value=1.0))
        s.add(models.FinancialFact(company_id=cid, fiscal_year=2024,
              concept="revenue", value=1.0))
        s.commit()

        counts = delete_company_and_children(s, "junkx")   # case-insensitive
        assert counts.get("companies") == 1
        assert counts.get("historical_financials", 0) >= 1
        assert s.query(models.Company).filter_by(ticker="JUNKX").first() is None
        assert s.query(models.HistoricalFinancial).filter_by(company_id=cid).count() == 0
        assert s.query(models.FinancialFact).filter_by(company_id=cid).count() == 0
        # idempotent — already gone
        assert delete_company_and_children(s, "JUNKX") == {}
    finally:
        s.close()
        Base.metadata.drop_all(engine)
