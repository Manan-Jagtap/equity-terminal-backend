"""Unit tests for the alternative valuation models (SOTP + insurer P/EV).

These live outside the parity-tested engine core; recommend() uses them to
override the intrinsic for conglomerates and life insurers."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")

from app.alt_models import sotp_value, pev_value, alternative_intrinsic, INSURER_EV


def test_sotp_reliance_sums_segments_less_net_debt():
    r = sotp_value("RELIANCE")
    assert r["method"] == "Sum-of-the-Parts"
    expected = (1100000 + 900000 + 450000 + 150000 - 120788) / 1601.78
    assert abs(r["intrinsic"] - expected) < 1e-6
    assert len(r["components"]) == 4


def test_sotp_unknown_is_none():
    assert sotp_value("TCS") is None
    assert sotp_value("") is None


def test_sotp_uses_live_share_count_not_preset(monkeypatch):
    # DAT-01 regression: the per-share intrinsic MUST divide by the live share
    # count passed in, not the hand-seeded preset. A live count 2x the preset
    # must halve the per-share fair value; an unchanged live count reproduces it.
    from app.alt_models import SOTP_PRESETS
    preset = SOTP_PRESETS["ONGC"]
    equity = sum(ev for _, ev in preset["segments"]) - preset["net_debt"]
    live_shares = preset["shares"] * 2
    r = sotp_value("ONGC", live_shares)
    assert abs(r["intrinsic"] - equity / live_shares) < 1e-6
    # exactly half the value it would print if it (wrongly) used the preset count
    assert abs(r["intrinsic"] - sotp_value("ONGC")["intrinsic"] / 2) < 1e-6


def test_alt_model_divergence_gate_blocks_confident_buy():
    # DAT-03 guard: an ILLUSTRATIVE alt-model (SOTP/insurer seeds) landing >60%
    # above the market must read LOW CONF, never BUY (ITC printed BUY +113% off
    # a stale cigarettes-segment EV bigger than its whole market cap).
    from app import engines
    from app.alt_models import SOTP_PRESETS
    tk = "ITC"
    preset = SOTP_PRESETS[tk]
    shares = preset["shares"]
    equity = sum(ev for _, ev in preset["segments"]) - (preset.get("net_debt") or 0)
    per_share = equity / shares
    lowball = per_share / 2.5          # price 60%+ below the SOTP output
    co = {"ticker": tk, "price": lowball, "shares": shares, "equity": equity * 0.4,
          "net_profit": equity * 0.06, "revenue": equity * 0.5, "net_debt": preset.get("net_debt") or 0,
          "type": "nonfinancial", "series": [], "synthetic_series": False}
    a = {"_valuation_sector": "MANUFACTURING", "risk_free": 0.069, "beta": 1.0,
         "erp": 0.05, "terminal_growth": 0.05, "rev_growth": 0.10, "ebit_margin": 0.30,
         "terminal_ebit_margin": 0.30, "tax_rate": 0.25, "sales_to_capital": 2.0,
         "years": 10, "wacc_floor_spread": 0.03}
    r = engines.recommend(co, a)
    assert r["method"] == "Sum-of-the-Parts"
    assert r["mos"] is not None and r["mos"] > 0.60
    assert r["verdict"] == "LOW CONF"
    assert any("Illustrative-input model" in (x.get("note") or "") for x in r["reasons"])


def test_alternative_intrinsic_sotp_divides_by_live_shares():
    # End-to-end through the router: a conglomerate whose live share count differs
    # from the preset must be valued on the live basis (the VEDL/BAJAJFINSV bug).
    from app.alt_models import SOTP_PRESETS
    tk = next(iter(SOTP_PRESETS))
    preset_sh = SOTP_PRESETS[tk]["shares"]
    live = alternative_intrinsic({"ticker": tk, "shares": preset_sh * 3},
                                 {"_valuation_sector": "MANUFACTURING"})
    stale = alternative_intrinsic({"ticker": tk},  # no live shares → preset fallback
                                  {"_valuation_sector": "MANUFACTURING"})
    assert abs(live["intrinsic"] - stale["intrinsic"] / 3) < 1e-4


def test_pev_uses_gordon_justified_multiple_without_vnb_seed():
    # Gordon justified-P/EV is the FALLBACK when no VNB is seeded.
    a = {"risk_free": 0.069, "beta": 0.90, "erp": 0.05, "terminal_growth": 0.055}
    saved = INSURER_EV["SBILIFE"].pop("vnb_per_share")
    try:
        r = pev_value("SBILIFE", a)
        ke = 0.069 + 0.90 * 0.05          # 0.114
        just = max(1.0, min(3.0, (0.197 - 0.055) / (ke - 0.055)))
        assert r["method"] == "P/EV Appraisal"
        assert abs(r["intrinsic"] - 805.40 * just) < 1e-6
    finally:
        INSURER_EV["SBILIFE"]["vnb_per_share"] = saved


def test_pev_multiple_is_clamped():
    # absurd RoEV must not produce an absurd multiple
    a = {"risk_free": 0.069, "beta": 0.90, "erp": 0.05, "terminal_growth": 0.05}
    # temporarily exercise the clamp via a high-RoEV name isn't seeded, so verify
    # the band on the seeded ones stays within [1,3]
    for tk in ("SBILIFE", "HDFCLIFE", "ICICIPRULI"):
        r = pev_value(tk, a)
        from app.alt_models import INSURER_EV
        mult = r["intrinsic"] / INSURER_EV[tk]["ev_per_share"]
        assert 1.0 <= mult <= 3.0


def test_lici_omitted_stays_unmodelled():
    # LIC's reported EV overstates shareholder value → intentionally not seeded,
    # so it falls back to the insurer LOW-CONF path rather than a misleading P/EV.
    assert pev_value("LICI", {"risk_free": 0.069, "beta": 0.9, "erp": 0.05}) is None


def test_pev_unknown_is_none():
    assert pev_value("TCS", {}) is None


def test_alternative_intrinsic_routes_by_sector():
    ins_a = {"_valuation_sector": "INSURANCE", "risk_free": 0.069, "beta": 0.9,
             "erp": 0.05, "terminal_growth": 0.055}
    assert alternative_intrinsic({"ticker": "HDFCLIFE"}, ins_a)["method"] == "EV + VNB Appraisal"
    assert alternative_intrinsic({"ticker": "ADANIENT"}, {"_valuation_sector": "MANUFACTURING"})["method"] == "Sum-of-the-Parts"
    assert alternative_intrinsic({"ticker": "TCS"}, {"_valuation_sector": "IT_SERVICES"}) is None


def test_appraisal_value_two_pieces_and_band():
    # SBILIFE model convention (institutional): AV = EV + VNB x multiple, and the
    # implied P/EV must stay inside the listed band (1.0-3.0x EV).
    a = {"risk_free": 0.069, "beta": 0.90, "erp": 0.05, "terminal_growth": 0.05}
    r = pev_value("SBILIFE", a)
    assert r["method"] == "EV + VNB Appraisal"
    ev = INSURER_EV["SBILIFE"]["ev_per_share"]
    assert r["intrinsic"] > ev                      # franchise adds value on top of the book
    assert 1.0 <= r["intrinsic"] / ev <= 3.0        # ...but stays anchored to it
    labels = [c["label"] for c in r["components"]]
    assert any("Embedded value" in l for l in labels)
    assert any("Structural value" in l for l in labels)


def test_appraisal_orders_by_franchise_quality():
    # Higher RoEV + VNB growth must earn a higher implied P/EV (SBILIFE > ICICIPRULI).
    a = {"risk_free": 0.069, "beta": 0.90, "erp": 0.05, "terminal_growth": 0.05}
    sbi = pev_value("SBILIFE", a); ipru = pev_value("ICICIPRULI", a)
    assert sbi["intrinsic"] / INSURER_EV["SBILIFE"]["ev_per_share"] > \
           ipru["intrinsic"] / INSURER_EV["ICICIPRULI"]["ev_per_share"]


def test_ongc_sotp_core_plus_stakes():
    # Upstream convention: core E&P at a through-cycle multiple + listed stakes
    # net of holdco discount. The core must dominate the stake sleeve.
    r = sotp_value("ONGC")
    assert r is not None and r["intrinsic"] > 0
    comps = {c["label"]: c["value"] for c in r["components"]}
    core = next(v for l, v in comps.items() if "Core E&P" in l)
    stakes = sum(v for l, v in comps.items() if "stake" in l or "investments" in l)
    assert core > stakes > 0
