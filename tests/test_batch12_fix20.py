"""Batch 12 — FIX-20 (FM-03): conviction- and volatility-aware position sizing.
The flat 3%/1.5% tranche is replaced by
  tranche = base × conviction-tier × inverse-vol scalar,
hard-capped per name and never larger than 5× the name's median daily traded
value. Pure helpers in portfolio_routes."""
from app.portfolio_routes import _conviction_tier, _vol_scalar, _tranche_size
from app.manager_engine import _annualised_vol


def _ev(sigma=None, adv_inr=None):
    return {"risk": {"sigma": sigma, "adv_inr": adv_inr}}


# ── tier + vol multipliers ────────────────────────────────────────────────────

def test_conviction_tiers():
    assert _conviction_tier(85)[0] == 1.5
    assert _conviction_tier(60)[0] == 1.0
    assert _conviction_tier(40)[0] == 0.5
    assert _conviction_tier(None)[0] == 0.5


def test_vol_scalar_buckets():
    assert _vol_scalar(0.18)[0] == 1.2      # calm compounder → bigger
    assert _vol_scalar(0.32)[0] == 1.0
    assert _vol_scalar(0.50)[0] == 0.8
    assert _vol_scalar(0.80)[0] == 0.6      # volatile trade → smaller
    assert _vol_scalar(None)[0] == 1.0      # unknown → neutral


# ── the composite tranche ─────────────────────────────────────────────────────

def test_size_differs_by_conviction():
    """Same base, same vol, different conviction → different rupee size."""
    total = 10_00_000  # ₹10L book
    hi, _ = _tranche_size(85, 0.03, total, _ev(sigma=0.30))   # 1.5× × 1.0×
    lo, _ = _tranche_size(50, 0.03, total, _ev(sigma=0.30))   # 0.5× × 1.0×
    assert hi > lo
    assert abs(hi - 0.045 * total) < 1        # 3% × 1.5
    assert abs(lo - 0.015 * total) < 1        # 3% × 0.5


def test_size_differs_by_vol():
    """Same conviction, different σ → calm name sized larger than volatile."""
    total = 10_00_000
    calm, _ = _tranche_size(60, 0.03, total, _ev(sigma=0.18))   # 1.0× × 1.2×
    wild, _ = _tranche_size(60, 0.03, total, _ev(sigma=0.80))   # 1.0× × 0.6×
    assert calm > wild
    assert abs(calm - 0.036 * total) < 1
    assert abs(wild - 0.018 * total) < 1


def test_hard_cap_per_name():
    """base × 1.5 × 1.2 = 5.4% is clamped to the 5% hard cap."""
    total = 10_00_000
    size, note = _tranche_size(90, 0.03, total, _ev(sigma=0.10))
    assert abs(size - 0.05 * total) < 1
    assert "5.0% of book" in note


def test_liquidity_cap_binds_for_thin_names():
    """A thin micro-cap: even a small % of a big book can't exceed 5× ADV."""
    total = 5_00_00_000  # ₹5cr book
    adv = 2_00_000       # ₹2L median daily value → cap 5×ADV = ₹10L
    size, note = _tranche_size(85, 0.03, total, _ev(sigma=0.30, adv_inr=adv))
    assert size == 5 * adv
    assert "liquidity" in note


def test_risk_off_base_halves_and_is_noted():
    total = 10_00_000
    size, note = _tranche_size(60, 0.015, total, _ev(sigma=0.30))
    assert abs(size - 0.015 * total) < 1      # 1.5% × 1.0 × 1.0
    assert "defensive tape" in note


def test_no_risk_block_degrades_gracefully():
    """ev=None (thin/evidence-pending name) → neutral vol, no liquidity cap."""
    total = 10_00_000
    size, _ = _tranche_size(60, 0.03, total, None)
    assert abs(size - 0.03 * total) < 1


# ── the σ estimator ───────────────────────────────────────────────────────────

def test_annualised_vol_sane_and_guarded():
    assert _annualised_vol([100.0] * 10) is None          # too little history
    flat = _annualised_vol([100.0] * 300)
    assert flat == 0.0                                     # no moves → zero vol
    import random
    random.seed(1)
    closes, p = [100.0], 100.0
    for _ in range(300):
        p *= (1 + random.gauss(0, 0.02)); closes.append(p)  # ~2%/day → ~32% ann
    v = _annualised_vol(closes)
    assert 0.20 < v < 0.45
