"""VAL-01 / VAL-03 — the plausibility-gate fixes from the 2026-07 valuation audit.

VAL-03: engines.price_gates is the ONE implementation of the price-dependent
gates, applied by both the batch engine and the intraday refresh_mos mirror.
VAL-01: a non-financial, non-alt BUY/ACC at mos > +50% must be corroborated by
the engine's own evidence (weighted-method dispersion ≤ 3× AND the sector-P/E
cross-check leg at/above price) or it reads LOW CONF.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")

from app import engines
from app.engines import price_gates
from app.ingest.compute_valuations import _verdict_from


# ── VAL-03: the shared hard cliffs ──────────────────────────────────────────

def test_lender_divergence_gate():
    assert price_gates("BUY", 0.85, is_financial=True, is_alt=False, high_roe=True) == "LOW CONF"
    assert price_gates("HOLD", 0.85, is_financial=True, is_alt=False, high_roe=True) == "HOLD"


def test_alt_divergence_gate():
    assert price_gates("ACCUMULATE", 0.70, is_financial=False, is_alt=True, high_roe=False) == "LOW CONF"
    assert price_gates("ACCUMULATE", 0.55, is_financial=False, is_alt=True, high_roe=False) == "ACCUMULATE"


def test_generic_implausible_upside_gate():
    for v in ("BUY", "ACCUMULATE", "HOLD", "REDUCE", "AVOID"):
        assert price_gates(v, 1.2, is_financial=False, is_alt=False, high_roe=False) == "LOW CONF"
    assert price_gates("BUY", 0.99, is_financial=False, is_alt=False, high_roe=False) == "BUY"


def test_compounder_downside_gate():
    assert price_gates("AVOID", -0.50, is_financial=False, is_alt=False, high_roe=True) == "LOW CONF"
    # low-return names keep their AVOID — genuinely rich
    assert price_gates("AVOID", -0.50, is_financial=False, is_alt=False, high_roe=False) == "AVOID"


def test_intraday_mirror_applies_gates():
    """The audit's VAL-03 scenario: a batch BUY (composite 70, reliable) whose
    price crashes intraday to mos +1.4 must NOT stay BUY."""
    assert _verdict_from(1.4, 70, True, "BUY") == "LOW CONF"
    # ordinary re-banding still works
    assert _verdict_from(0.20, 70, True, "BUY") == "BUY"
    assert _verdict_from(-0.30, 40, True, "HOLD") == "AVOID"
    # lender crash intraday
    assert _verdict_from(0.9, 70, True, "BUY", is_financial=True) == "LOW CONF"
    # sticky special states never upgrade on a tick
    assert _verdict_from(0.20, 70, True, "LOW CONF") == "LOW CONF"
    assert _verdict_from(0.20, 70, True, "NO DATA") == "NO DATA"


# ── VAL-01: corroboration band in recommend() ───────────────────────────────

def _co(price=160.0, fin=False):
    return {
        "ticker": "TESTCO", "type": "financial" if fin else "nonfinancial",
        "price": price, "shares": 10.0, "equity": 500.0, "net_profit": 100.0,
        "revenue": 1000.0, "net_debt": 50.0, "series": [], "sector": "Manufacturing",
    }


def _a():
    return {"risk_free": 0.07, "beta": 1.0, "erp": 0.05, "rev_growth": 0.10,
            "ebit_margin": 0.20, "tax_rate": 0.25, "reinvest_rate": 0.30,
            "debt_weight": 0.10, "cost_debt": 0.085, "fade_years": 8,
            "terminal_growth": 0.05, "forecast_roe": 0.15, "terminal_roe": 0.15,
            "payout": 0.25, "_valuation_sector": "MANUFACTURING"}


def _fake_blended(components):
    vals = [c["value"] for c in components if (c.get("weight") or 0) > 0 and c.get("value")]
    wsum = sum(c["weight"] for c in components if c.get("value"))
    blend = sum(c["value"] * c["weight"] for c in components if c.get("value")) / (wsum or 1)
    def fake(co, a):
        return {"blended": blend, "components": components, "primary": vals[0],
                "primary_method": "FCFF DCF",
                "valuation": {"intrinsic": vals[0], "method": "FCFF DCF", "rows": [],
                              "ke": 0.12, "wacc": 0.11, "bvps0": None, "ev": None,
                              "pv_explicit": 1.0, "tv_pv": 1.0}}
    return fake


def test_uncorroborated_big_upside_reads_low_conf(monkeypatch):
    """AWL-class: BUY-zone MoS ~+0.9 but the engine's own methods disagree
    (dispersion > 3×) → LOW CONF with the corroboration note."""
    comps = [{"method": "FCFF DCF", "value": 280.0, "weight": 0.55},
             {"method": "Exit Multiple", "value": 900.0, "weight": 0.30},
             {"method": "P/E (sector)", "value": 240.0, "weight": 0.15}]
    monkeypatch.setattr(engines, "blended", _fake_blended(comps))
    # price 300 puts the blend (~460) at mos ≈ +0.53 — inside the NEW band,
    # below the pre-existing +100% cliff (which its own test covers above).
    rec = engines.recommend(_co(price=300.0), _a())
    assert rec["mos"] is not None and 0.5 < rec["mos"] < 1.0
    assert rec["verdict"] == "LOW CONF"
    assert any("without corroboration" in (r.get("note") or "") for r in rec["reasons"])


def test_corroborated_deep_value_keeps_its_call(monkeypatch):
    """COALINDIA-class: agreeing methods + P/E leg above price → the confident
    call survives (the gate must not kill corroborated deep value)."""
    comps = [{"method": "FCFF DCF", "value": 280.0, "weight": 0.55},
             {"method": "Exit Multiple", "value": 320.0, "weight": 0.30},
             {"method": "P/E (sector)", "value": 310.0, "weight": 0.15}]
    monkeypatch.setattr(engines, "blended", _fake_blended(comps))
    rec = engines.recommend(_co(), _a())
    assert rec["mos"] is not None and rec["mos"] > 0.5
    assert rec["verdict"] in ("BUY", "ACCUMULATE")


def test_pe_leg_below_price_fails_corroboration(monkeypatch):
    comps = [{"method": "FCFF DCF", "value": 280.0, "weight": 0.55},
             {"method": "Exit Multiple", "value": 300.0, "weight": 0.30},
             {"method": "P/E (sector)", "value": 140.0, "weight": 0.15}]
    monkeypatch.setattr(engines, "blended", _fake_blended(comps))
    rec = engines.recommend(_co(), _a())
    assert rec["mos"] is not None and rec["mos"] > 0.5
    assert rec["verdict"] == "LOW CONF"


def test_band_only_applies_above_half(monkeypatch):
    """mos ≤ 0.50 is untouched by the corroboration band (existing behavior)."""
    comps = [{"method": "FCFF DCF", "value": 200.0, "weight": 0.55},
             {"method": "Exit Multiple", "value": 700.0, "weight": 0.30},
             {"method": "P/E (sector)", "value": 120.0, "weight": 0.15}]
    monkeypatch.setattr(engines, "blended", _fake_blended(comps))
    rec = engines.recommend(_co(price=250.0), _a())
    assert rec["mos"] is not None and rec["mos"] < 0.5
    assert rec["verdict"] != "LOW CONF" or rec["mos"] <= 0.5
