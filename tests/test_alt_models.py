"""Unit tests for the alternative valuation models (SOTP + insurer P/EV).

These live outside the parity-tested engine core; recommend() uses them to
override the intrinsic for conglomerates and life insurers."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")

from app.alt_models import sotp_value, pev_value, alternative_intrinsic


def test_sotp_reliance_sums_segments_less_net_debt():
    r = sotp_value("RELIANCE")
    assert r["method"] == "Sum-of-the-Parts"
    expected = (1100000 + 900000 + 450000 + 150000 - 120788) / 1601.78
    assert abs(r["intrinsic"] - expected) < 1e-6
    assert len(r["components"]) == 4


def test_sotp_unknown_is_none():
    assert sotp_value("TCS") is None
    assert sotp_value("") is None


def test_pev_uses_gordon_justified_multiple():
    a = {"risk_free": 0.069, "beta": 0.90, "erp": 0.05, "terminal_growth": 0.055}
    r = pev_value("SBILIFE", a)
    ke = 0.069 + 0.90 * 0.05          # 0.114
    just = max(1.0, min(3.0, (0.197 - 0.055) / (ke - 0.055)))
    assert r["method"] == "P/EV Appraisal"
    assert abs(r["intrinsic"] - 805.40 * just) < 1e-6


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
    assert alternative_intrinsic({"ticker": "HDFCLIFE"}, ins_a)["method"] == "P/EV Appraisal"
    assert alternative_intrinsic({"ticker": "ADANIENT"}, {"_valuation_sector": "MANUFACTURING"})["method"] == "Sum-of-the-Parts"
    assert alternative_intrinsic({"ticker": "TCS"}, {"_valuation_sector": "IT_SERVICES"}) is None
