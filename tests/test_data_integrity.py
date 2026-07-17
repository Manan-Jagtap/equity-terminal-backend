"""Unit tests for the continuous data-integrity sweep — every check must fire
on a synthetic bad row and stay quiet on a clean one."""
import os, sys
import datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import pytest
from app.database import Base, engine, SessionLocal
from app import models
from app.data_integrity import run_integrity_sweep, store_sweep, load_sweep


@pytest.fixture()
def db():
    Base.metadata.create_all(engine)
    s = SessionLocal()
    yield s
    s.close()
    Base.metadata.drop_all(engine)


def _co(db, ticker, name, shares=100.0):
    co = models.Company(ticker=ticker, name=name, sector="IT",
                        type="nonfinancial", shares_outstanding=shares)
    db.add(co); db.flush()
    return co


def test_clean_universe_is_green(db):
    co = _co(db, "GOOD", "Good Co")
    db.add(models.MarketSnapshot(company_id=co.id, price=100.0))
    db.add(models.Valuation(company_id=co.id, mos=0.2, verdict="BUY",
                            confidence="high", pe=20.0, pb=3.0, roe=0.18))
    db.commit()
    out = run_integrity_sweep(db)
    assert out["status"] == "green" and out["n_findings"] == 0
    assert out["verdicts"] == {"BUY": 1}


def test_every_check_fires(db):
    # duplicate display name
    _co(db, "AAA", "Same Name")
    _co(db, "BBB", "Same Name")
    # BUY with negative MoS + low-conf ACCUMULATE + ungated absurd MoS + AVOID positive
    for tk, verdict, mos, conf in (("CCC", "BUY", -0.2, "high"),
                                   ("DDD", "ACCUMULATE", 0.3, "low"),
                                   ("EEE", "HOLD", 9.0, "high"),
                                   ("FFF", "AVOID", 0.4, "high")):
        c = _co(db, tk, f"{tk} Co")
        db.add(models.Valuation(company_id=c.id, mos=mos, verdict=verdict,
                                confidence=conf))
    # absurd price + stale price
    g = _co(db, "GGG", "G Co")
    db.add(models.MarketSnapshot(company_id=g.id, price=-5.0))
    h = _co(db, "HHH", "H Co")
    db.add(models.MarketSnapshot(company_id=h.id, price=100.0,
                                 as_of=dt.datetime.utcnow() - dt.timedelta(days=30)))
    # statement identity: net worth > total assets
    i = _co(db, "III", "I Co")
    for item, val in (("net_worth", 500.0), ("total_assets", 100.0)):
        db.add(models.HistoricalFinancial(company_id=i.id, fiscal_year=2025,
                                          statement_type="BS", line_item=item, value=val))
    db.commit()

    out = run_integrity_sweep(db)
    checks = set(out["by_check"])
    for expected in ("duplicate_name", "verdict_mos_incoherent", "buy_on_low_conf",
                     "mos_absurd_ungated", "price_absurd", "price_stale",
                     "networth_gt_assets"):
        assert expected in checks, f"{expected} did not fire: {checks}"
    assert out["status"] == "red"          # P1s present


def test_low_conf_gates_absurd_mos(db):
    c = _co(db, "JJJ", "J Co")
    db.add(models.Valuation(company_id=c.id, mos=11.5, verdict="LOW CONF",
                            confidence="low"))
    db.commit()
    out = run_integrity_sweep(db)
    assert "mos_absurd_ungated" not in out["by_check"]


def test_store_and_load_roundtrip(db):
    _co(db, "KKK", "K Co")
    db.commit()
    stored = store_sweep(db)
    loaded = load_sweep(db)
    assert loaded["as_of"] == stored["as_of"]
    assert loaded["n_companies"] == 1
