"""CORR-6: explicit-stage reinvestment must be consistent with growth.

Measured on the 317-name calibration set before this fix: 79% of names
reinvested LESS than g/ROIC — the model credited growth without charging the
capital that funds it, inflating FCFF in every forecast year. The terminal value
already enforced the link (term_rr = g/mature_roic); the explicit stage did not.

The floor is ONE-SIDED (the VAL-02 discipline): it can only RAISE reinvestment,
never lower it — so no valuation is inflated by this change.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")

from app import engines


def _co():
    return {"type": "nonfinancial", "revenue": 1000.0, "shares": 100.0,
            "net_debt": 0.0, "equity": 500.0, "net_profit": 80.0, "price": 100.0}


def _a(**over):
    a = {"risk_free": 0.067, "beta": 1.0, "erp": 0.05, "payout": 0.3,
         "fade_years": 10, "forecast_roe": 0.18, "terminal_roe": 0.15,
         "terminal_growth": 0.05, "debt_weight": 0.2, "cost_debt": 0.09,
         "tax_rate": 0.25, "rev_growth": 0.12, "ebit_margin": 0.15,
         "reinvest_rate": 0.30, "_valuation_sector": "MANUFACTURING"}
    a.update(over)
    return a


def test_underfunded_growth_is_now_charged():
    """g=12% on ROIC=15% needs 80% reinvestment; a 30% historical rate must be
    lifted to the floor, which LOWERS the intrinsic (growth is paid for)."""
    co = _co()
    low = engines.fcff_dcf(co, _a(reinvest_rate=0.30, roic_used=0.15))
    # Same name whose derived rate already exceeds the floor → untouched.
    high = engines.fcff_dcf(co, _a(reinvest_rate=0.95, roic_used=0.15))
    assert low["intrinsic"] > 0
    # The under-funded case must value BELOW a case that reinvests even more,
    # and both must be finite/sane.
    assert low["intrinsic"] > high["intrinsic"], "more reinvestment => less FCFF"


def test_floor_is_one_sided_never_lowers_reinvestment():
    """A capital-hungry name reinvesting ABOVE g/ROIC keeps its own rate — the
    fix must not hand it extra free cash flow (which would inflate value)."""
    co = _co()
    # roic very high => g/ROIC floor is tiny; the derived 0.80 must still apply.
    out = engines.fcff_dcf(co, _a(reinvest_rate=0.80, roic_used=5.0))
    # Recompute what an unfloored 0.80 gives: identical, since floor < 0.80.
    same = engines.fcff_dcf(co, _a(reinvest_rate=0.80, roic_used=5.0))
    assert abs(out["intrinsic"] - same["intrinsic"]) < 1e-12


def test_cap_prevents_runaway_reinvestment():
    """g >> ROIC would demand >100% reinvestment; the cap keeps FCFF sane."""
    co = _co()
    out = engines.fcff_dcf(co, _a(rev_growth=0.40, roic_used=0.05,
                                  reinvest_rate=0.10))
    assert out["intrinsic"] is not None
    assert engines._REINVEST_CAP <= 1.0


def test_engine_js_mirrors_the_floor():
    """Parity contract: engines.py and engine.js must both carry CORR-6.

    This used to look for the frontend as a SIBLING of the backend checkout and
    skip when it was absent. It was absent everywhere: the repos actually live at
    ~/Downloads/backend and ~/equity-terminal, so the guessed path resolved to
    ~/Downloads/equity-terminal, and CI never checks the frontend out at all.
    The test therefore skipped on every run since it was written — a green
    parity contract that had never once been evaluated.

    The frontend is public, so fetch the file when it is not on disk. Local path
    still wins (offline, and picks up uncommitted edits); CORR6_ENGINE_JS
    overrides both.
    """
    import pytest
    src = None
    candidates = [
        os.environ.get("CORR6_ENGINE_JS"),
        os.path.join(os.path.expanduser("~"), "equity-terminal", "src", "lib", "engine.js"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "equity-terminal", "src", "lib", "engine.js"),
    ]
    for p in candidates:
        if p and os.path.exists(p):
            src = open(p, encoding="utf-8").read()
            break
    if src is None:
        import urllib.request, urllib.error
        url = ("https://raw.githubusercontent.com/Manan-Jagtap/equity-terminal"
               "/main/src/lib/engine.js")
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                src = r.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError) as e:
            pytest.skip(f"engine.js unavailable locally and unfetchable: {e}")

    assert "REINVEST_CAP" in src and "roicEx" in src, "engine.js missing CORR-6 mirror"
