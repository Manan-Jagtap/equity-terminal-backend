"""Batch 14 — FIX-22: self-trust un-inert (FM-06/ENG-07), turnover discipline
(FM-05), and the NIFTY benchmark (FM-07/ENG-09)."""
import datetime as dt
import app.benchmark as bench
from app.portfolio_routes import (_cost_line, _turnover_caveat, benchmark_block,
                                  _verdict_age_by)


# ── FM-05: turnover / cost ────────────────────────────────────────────────────

def test_cost_line():
    assert _cost_line(0) is None
    c = _cost_line(100000)
    assert c["bps"] == 30 and c["inr"] == 300 and "round trip" in c["note"]


def test_turnover_caveat_matrix():
    assert _turnover_caveat("REVIEW TRIM", "REDUCE", 3)         # fresh signal → caveat
    assert _turnover_caveat("REVIEW EXIT", "REDUCE", 9)
    assert _turnover_caveat("REVIEW TRIM", "REDUCE", 20) is None   # aged → act
    assert _turnover_caveat("ADD", "BUY", 1) is None              # not a REVIEW
    assert _turnover_caveat("REVIEW EXIT", "SELL", 1) is None     # decisive SELL overrides


# ── FM-07 / ENG-09: NIFTY TRI proxy ───────────────────────────────────────────

def test_nifty_tr_proxy_adds_yield(monkeypatch):
    # a flat price index one year apart → price return 0, TR proxy ≈ +1.3%
    y0 = (dt.date.today() - dt.timedelta(days=365)).isoformat()
    monkeypatch.setattr(bench, "nifty_series",
                        lambda: ([y0, dt.date.today().isoformat()], [100.0, 100.0]))
    assert bench.nifty_return(y0) == 0.0
    tr = bench.nifty_tr_return(y0)
    assert abs(tr - bench.NIFTY_DIV_YIELD) < 2e-3               # ~1 yr of yield


def test_nifty_helpers_degrade_without_feed(monkeypatch):
    monkeypatch.setattr(bench, "nifty_series", lambda: None)
    assert bench.nifty_return("2025-01-01") is None
    assert bench.nifty_tr_return("2025-01-01") is None


def test_benchmark_block_is_tri_proxy(monkeypatch):
    # index flat over a year; a book that exactly matched it on price would still
    # trail the TR proxy by the accrued dividend yield → alpha slightly negative.
    y0 = (dt.date.today() - dt.timedelta(days=365)).isoformat()
    import app.portfolio_routes as pr
    monkeypatch.setattr(pr, "_nifty_series",
                        lambda: ([y0, dt.date.today().isoformat()], [100.0, 100.0]))
    items = [{"cost": 100000.0, "buy_date": y0, "value": 100000.0, "div_income": 0.0}]
    b = benchmark_block(items)
    assert b["benchmark"] == "NIFTY 50 TR (proxy)"
    assert b["benchmark_return"] > 0                            # yield accrued
    assert b["alpha"] < 0                                       # price-flat book trails the TRI proxy


# ── FM-06 / ENG-07: self-trust un-inert + de-tautologised ─────────────────────

def _seed_trust_db():
    from app.database import SessionLocal, engine, Base
    from app import models
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    # wipe any prior rows so the test is deterministic
    db.query(models.VerdictSnapshot).delete()
    db.query(models.MarketSnapshot).delete()
    db.query(models.Company).filter(models.Company.ticker.like("TST%")).delete()
    db.commit()
    aged = (dt.date.today() - dt.timedelta(days=150)).isoformat()
    cid = 90000
    # Sector WIN: 6 BUYs that all rose; Sector LOSE: 6 BUYs that all fell.
    for sec, rose in (("WINSEC", True), ("LOSESEC", False)):
        for i in range(6):
            cid += 1
            tk = f"TST{cid}"
            db.add(models.Company(id=cid, ticker=tk, name=tk, sector=sec, type="nonfin",
                                  shares_outstanding=1.0e7))
            db.add(models.VerdictSnapshot(company_id=cid, ticker=tk, date=aged,
                                          price=100.0, verdict="BUY", valuation_sector=sec))
            db.add(models.MarketSnapshot(company_id=cid, price=(130.0 if rose else 70.0)))
    db.commit()
    return db


def test_model_trust_un_inert_and_not_tautological():
    from app.manager_engine import model_trust_by_sector
    db = _seed_trust_db()
    try:
        trust = model_trust_by_sector(db)   # no NIFTY feed in test → beat-zero fallback
    finally:
        db.close()
    # un-inert: at least one sector scored (prod had 0)
    assert trust, "trust must not be empty"
    # de-tautologised: a sector whose BUYs all won scores high, all-lost scores the floor
    # — NOT the ~0.75 the old 'beat own median' pinned everything to.
    assert trust.get("WINSEC") == 1.0
    assert trust.get("LOSESEC") == 0.3


def test_verdict_age_by_counts_the_current_run():
    from app.database import SessionLocal, engine, Base
    from app import models
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.query(models.VerdictSnapshot).filter(models.VerdictSnapshot.ticker == "TSTAGE").delete()
    db.commit()
    base = dt.date.today()
    # newest→oldest: HOLD, HOLD, HOLD, REDUCE, REDUCE  → current run (HOLD) = 3
    seq = [("HOLD", 0), ("HOLD", 1), ("HOLD", 2), ("REDUCE", 3), ("REDUCE", 4)]
    for v, ago in seq:
        db.add(models.VerdictSnapshot(company_id=99001, ticker="TSTAGE",
                                      date=(base - dt.timedelta(days=ago)).isoformat(),
                                      price=100.0, verdict=v))
    db.commit()
    try:
        age = _verdict_age_by(db, {"TSTAGE"})
    finally:
        db.close()
    assert age["TSTAGE"] == 3
