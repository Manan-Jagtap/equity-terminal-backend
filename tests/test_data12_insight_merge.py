"""DATA-12: the CompanyInsight blob must MERGE, never blind-replace.

Live incident 24 Jul 2026: /documents started answering 404 "Endpoint not
allowed" and /historical_stats "Not a valid script_code" — read at the time as
a plan change, corrected 25 Aug 2026 to our calling the wrong vendor host (the
Developer plan's own is dev.indianapi.in). The cause is irrelevant to this
test: whatever silences an endpoint, the blob must merge. Those parsers
returned None, `_insights` dropped the empty keys,
and the old `row.data = data` full replace DELETED the last-good stored values —
documents for ~378 names and quarterly results for ~736. Same purge-before-
validate class as DATA-02 (statements), which _swap_statements already guards.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")


def test_insight_write_merges_and_keeps_last_good_subkeys():
    """A fresh blob missing `documents`/`results` must NOT delete the stored
    ones, while keys present in the fresh blob still win."""
    from app.database import SessionLocal, engine, Base
    from app import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    co = models.Company(ticker="D12TEST", name="Data12 Test Co", type="nonfinancial",
                        sector="Testing", shares_outstanding=100.0)
    db.add(co); db.commit(); db.refresh(co)
    cid = int(co.id)                        # plain int: survives session close
    db.add(models.CompanyInsight(company_id=cid, ticker_id="S0000001", data={
        "documents": {"annual_reports": [{"year": 2025, "url": "u"}]},
        "results": {"latest": "Q1FY27"},
        "ratios": {"pe": 20.0},
    }))
    db.commit()

    # Simulate the post-incident write: vendor endpoints gone → those keys absent.
    fresh = {"ratios": {"pe": 25.0}, "analyst": {"num_analysts": 3}}
    row = db.query(models.CompanyInsight).filter_by(company_id=cid).first()
    merged = dict(row.data or {})
    merged.update(fresh)                    # the shipped merge semantics
    row.data = merged
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(row, "data")
    db.commit()
    db.close()

    db = SessionLocal()                     # fresh session — must survive
    got = (db.query(models.CompanyInsight)
             .filter_by(company_id=cid).first().data or {})
    assert got.get("documents"), "documents were WIPED — the DATA-12 regression"
    assert got.get("results"), "results were WIPED — the DATA-12 regression"
    assert got["ratios"]["pe"] == 25.0      # fresh value wins
    assert got["analyst"]["num_analysts"] == 3   # new key added
    db.query(models.CompanyInsight).filter_by(company_id=cid).delete()
    db.query(models.Company).filter_by(id=cid).delete()
    db.commit(); db.close()


def test_ingester_uses_merge_not_blind_replace():
    """Guard the source itself: a future edit must not reintroduce the wipe."""
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                        "app", "ingest", "indianapi_ingester.py")
    src = open(path, encoding="utf-8").read()
    assert "merged = dict(row.data or {})" in src and "merged.update(data)" in src
    assert "row.ticker_id, row.data = tid, data" not in src, \
        "blind replace of the insight blob is back — DATA-12 would recur"
