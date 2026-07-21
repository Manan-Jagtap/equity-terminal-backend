"""Batch 16 — FIX-24: alpha factor hygiene (ENG-02/03/12/14/16)."""
import datetime as dt
from app.factors import _pct_ranks, score_universe, portfolio_xray
from app.forensics import forensic_report


# ── ENG-14: tie-averaged ranks → order-invariant scores ───────────────────────

def test_pct_ranks_ties_are_order_invariant():
    a = _pct_ranks([("a", 5), ("b", 5), ("c", 9), ("d", 1)])
    b = _pct_ranks([("d", 1), ("c", 9), ("b", 5), ("a", 5)])
    assert a == b
    assert a["a"] == a["b"] == 50.0          # tied values share the averaged percentile


def test_score_universe_invariant_to_row_order():
    import random
    rows = [{"ticker": f"T{i}", "mos": 0.1, "roe": 0.15, "pe": 20, "pb": 4,
             "closes": [], "growth": (i % 3) * 0.05, "catalyst": (i % 2) * 0.1}
            for i in range(12)]
    a = {r["ticker"]: r["alpha_score"] for r in score_universe(rows)}
    shuffled = rows[:]; random.Random(7).shuffle(shuffled)
    b = {r["ticker"]: r["alpha_score"] for r in score_universe(shuffled)}
    assert a == b                            # identical scores incl. ties


# ── ENG-14: ≥3-factor gate + momentum_basis ───────────────────────────────────

def test_three_factor_gate():
    two = score_universe([{"ticker": "X", "mos": 0.1, "roe": 0.2, "pe": None, "pb": None,
                           "closes": [], "growth": None}])
    assert two[0]["alpha_score"] is None and "3" in two[0]["alpha_reason"]
    three = score_universe([{"ticker": "X", "mos": 0.1, "roe": 0.2, "pe": 20, "pb": 4,
                             "closes": [], "growth": 0.1}])
    assert three[0]["alpha_score"] is not None and three[0]["alpha_reason"] is None


def test_momentum_basis_tag():
    long_series = list(range(1, 300))
    r = score_universe([{"ticker": "L", "mos": 0.1, "roe": 0.2, "pe": 20, "pb": 4,
                         "closes": long_series, "growth": 0.1}])[0]
    assert r["momentum_basis"] == "12-1"
    short = score_universe([{"ticker": "S", "mos": 0.1, "roe": 0.2, "pe": 20, "pb": 4,
                             "closes": list(range(1, 160)), "growth": 0.1}])[0]
    assert short["momentum_basis"] == "6-1"


# ── ENG-16: correlation-aware vol note + alias ────────────────────────────────

def test_portfolio_xray_vol_note_and_alias():
    items = [{"ticker": "A", "sector": "IT", "value": 5000, "alpha_score": 70,
              "factors": {"value": 60, "quality": 70, "momentum": 55}, "volatility": 0.30}]
    x = portfolio_xray(items)
    assert x["avg_position_vol"] == x["est_volatility"]      # alias kept
    assert "ignores diversification" in x["vol_note"]


# ── ENG-12: Sloan flag on SIGNED accruals ─────────────────────────────────────

def _statements(pat, cfo, ta=200.0):
    return {2024: {"PL": {"pat": pat * 0.9}, "CF": {"operating_cf": cfo * 0.9},
                   "BS": {"total_assets": ta}},
            2025: {"PL": {"pat": pat}, "CF": {"operating_cf": cfo},
                   "BS": {"total_assets": ta}}}


def test_sloan_positive_accruals_red():
    # PAT ≫ CFO (accruals prop up earnings) → red "High accruals"
    fr = forensic_report(_statements(pat=100.0, cfo=20.0), {"type": "nonfin"})
    assert any(f["label"] == "High accruals" and f["level"] == "red" for f in fr["flags"])


def test_sloan_negative_accruals_not_red():
    # PAT=100, CFO=160, TA=200 → accr = −0.30: conservative, NOT the Sloan sin.
    fr = forensic_report(_statements(pat=100.0, cfo=160.0), {"type": "nonfin"})
    assert not any(f["label"] == "High accruals" for f in fr["flags"])   # no red
    assert fr["metrics"]["accrual_ratio"]["flag"] == "amber"
    assert "cash-rich" in fr["metrics"]["accrual_ratio"]["note"]


# ── ENG-03: catalyst uses a bounded trailing window, not since-inception ───────

def test_catalyst_window_uses_60d_prior():
    from app.database import SessionLocal, engine, Base
    from app import models
    from app.signals import catalyst_by
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.query(models.ConsensusSnapshot).filter(models.ConsensusSnapshot.ticker == "TSTCAT").delete()
    db.commit()
    today = dt.date.today()
    # since-inception (h[0], 120d) would give +0.10; the 60d-window prior (90d) gives −0.20.
    for ago, up in [(120, 0.10), (90, 0.40), (30, 0.55), (0, 0.20)]:
        db.add(models.ConsensusSnapshot(company_id=99500, ticker="TSTCAT",
                                        date=(today - dt.timedelta(days=ago)).isoformat(),
                                        upside=up))
    db.commit()
    try:
        cat = catalyst_by(db)
    finally:
        db.close()
    assert abs(cat["TSTCAT"] - (-0.20)) < 1e-9     # 0.20 (latest) − 0.40 (90d prior)
