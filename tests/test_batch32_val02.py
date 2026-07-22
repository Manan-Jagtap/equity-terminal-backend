"""Batch 32 — VAL-02 earned compounder terminal ROIC + TOBACCO sector.

The terminal value faded EVERY nonfin business to the sector mature ROIC,
pricing DMART/HUL-class franchises as if their return advantage dies entirely
in perpetuity (the residual compounder level-bias). A PROVEN durable franchise
now fades only halfway to sector — strictly gated, one-sided, bounded.
Cigarettes leave CONSUMER (28x EV/EBITDA) for a TOBACCO param (11x)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")

from app import sector_params as SP
from app.derive import derive_assumptions
from app import engines


def _stmts(years, rev0=1000.0, margin=0.25, roic_scale=3.0):
    """Synthetic nonfin statements: high-margin, capital-light franchise.
    roic_scale ~ EBIT/net-worth proxy; small net worth → high operating ROIC."""
    out = {}
    for i, y in enumerate(range(2026 - years + 1, 2027)):
        rev = rev0 * (1.10 ** i)
        ebit = rev * margin
        out[y] = {"PL": {"revenue": rev, "ebit": ebit, "pat": ebit * 0.72,
                         "pbt": ebit * 0.95, "tax": ebit * 0.23},
                  "BS": {"net_worth": ebit / roic_scale * 4.0, "equity": 10.0,
                         "borrowings": 0.0},
                  "CF": {"capex": -rev * 0.02, "dividends": -ebit * 0.3}}
    return out


def test_durable_franchise_earns_terminal_roic():
    a = derive_assumptions(_stmts(8), "CONSUMER", False)
    assert a.get("terminal_roic") is not None
    p = SP.SECTOR_PARAMS["CONSUMER"]
    # one-sided + bounded: above sector, never above 1.5x sector / 0.40
    assert p["mature_roic"] < a["terminal_roic"] <= min(1.5 * p["mature_roic"], 0.40)
    assert "terminal_roic" in a["_drivers"]


def test_short_history_gets_no_lift():
    a = derive_assumptions(_stmts(4), "CONSUMER", False)   # <6y — not proven durable
    assert a.get("terminal_roic") is None


def test_cyclical_never_lifts():
    a = derive_assumptions(_stmts(8), "METAL", False)      # cyclical sector
    assert a.get("terminal_roic") is None


def test_ordinary_roic_gets_no_lift():
    # low-margin, capital-heavy → roic_used pins at the sector floor (<1.4x)
    a = derive_assumptions(_stmts(8, margin=0.08, roic_scale=0.4), "CONSUMER", False)
    assert a.get("terminal_roic") is None


def test_terminal_roic_raises_fcff_intrinsic_one_sided():
    a = derive_assumptions(_stmts(8), "CONSUMER", False)
    co = {"type": "nonfinancial", "revenue": 2000.0, "net_debt": -100.0, "shares": 10.0}
    hi = engines.fcff_dcf(co, a)["intrinsic"]
    lo = engines.fcff_dcf(co, {**a, "terminal_roic": None})["intrinsic"]
    assert hi > lo                       # earned terminal only ever LIFTS


def test_tobacco_classification_and_params():
    assert SP.classify_valuation_sector("Tobacco", None) == "TOBACCO"
    assert SP.classify_valuation_sector("Cigarettes & Tobacco", None) == "TOBACCO"
    for tk in ("ITC", "GODFRYPHLP", "VSTIND"):
        assert SP.TICKER_OVERRIDES[tk] == "TOBACCO"
    p = SP.SECTOR_PARAMS["TOBACCO"]
    assert p["exit_ev_ebitda"] == 11 and p["exit_pe"] == 20   # not staples' 28x/34x


def test_furniture_maps_to_consumer_disc():
    assert SP.classify_valuation_sector("Furniture & Fixtures", None) == "CONSUMER_DISC"
