"""Every PUBLIC surface must honour a withheld fair value — not just the JSON.

SPEC_DAT13_SUPPRESS_VALUE.md required this and named the failure mode in its
Risks section: "any surface that forgets the flag will print the number again."
Three surfaces forgot simultaneously. Measured live 2026-08-04: 111 of 997 names
had intrinsic:null in /api/companies while GET /api/export/ADANIGREEN.xlsx
printed "Fair value / share (Rs) 64.36" and "Margin of safety -0.9545".

These tests exist so a FOURTH surface cannot regress silently.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from app.valuation_public import (
    SUPPRESSING_GATES, FAIR_VALUE_NM, NM_CELL,
    apply_value_suppression, is_suppressed_rec, is_suppressed_row,
)


class _Row:
    """Minimal stand-in for a stored models.Valuation row."""
    def __init__(self, gate, intrinsic=64.36, mos=-0.9545):
        self.gate_state, self.intrinsic, self.mos = gate, intrinsic, mos


# ── the contract itself ──────────────────────────────────────────────────────
def test_every_suppressing_gate_is_detected_on_a_stored_row():
    for gate in SUPPRESSING_GATES:
        assert is_suppressed_row(_Row(gate)) is True, gate
    assert is_suppressed_row(_Row("clean")) is False
    assert is_suppressed_row(None) is False


def test_live_rec_detected_by_flag_or_gate():
    assert is_suppressed_rec({"value_suppressed": True}) is True
    for gate in SUPPRESSING_GATES:
        assert is_suppressed_rec({"gate_state": gate}) is True
    assert is_suppressed_rec({"gate_state": "clean"}) is False
    assert is_suppressed_rec({}) is False
    assert is_suppressed_rec(None) is False


def test_suppression_nulls_every_value_key_and_is_idempotent():
    p = {"intrinsic": 64.36, "mos": -0.9545, "blended": 64.36, "verdict": "AVOID"}
    out = apply_value_suppression(p, True)
    assert out["intrinsic"] is None and out["mos"] is None and out["blended"] is None
    assert out["verdict"] == "AVOID", "the CALL must survive; only the number goes"
    assert out["fair_value_note"] == FAIR_VALUE_NM
    again = apply_value_suppression(dict(out), True)
    assert again["intrinsic"] is None


def test_unsuppressed_payload_is_untouched():
    p = {"intrinsic": 2954.9, "mos": 0.208, "verdict": "BUY"}
    assert apply_value_suppression(dict(p), False) == p


# ── the surfaces ─────────────────────────────────────────────────────────────
def _summary_cells(rec):
    """Build a REAL summary sheet via the shipped code and return {label: value}.

    This calls export_routes._summary_sheet rather than re-implementing its cell
    logic. An earlier version of this test rebuilt the `NM_CELL if sup else ...`
    expression inline and therefore passed with the fix disabled — it asserted
    that my test file was consistent with itself, which is worth nothing.
    """
    from openpyxl import Workbook
    from app.export_routes import _summary_sheet

    class _Co:
        ticker, name, sector, type = "TESTCO", "Test Co", "Industrials", "nonfinancial"
        shares_outstanding, bse_scrip_code, template_code = 100.0, None, None
    wb = Workbook(); ws = wb.active
    _summary_sheet(ws, _Co(), 1413.4, rec, None, None)
    out = {}
    for row in ws.iter_rows():
        label = row[0].value
        if isinstance(label, str) and row[1].value is not None:
            out.setdefault(label, row[1].value)
    return out


def test_company_workbook_hides_the_value_when_suppressed():
    """The REAL summary sheet must not carry the withheld figure."""
    rec = {"intrinsic": 64.36, "mos": -0.9545, "composite": 41.0,
           "verdict": "AVOID", "value_suppressed": True,
           "valuation": {}, "primary_method": "FCFF DCF"}
    cells = _summary_cells(rec)
    fv = cells.get("Fair value / share (Rs)")
    mos = cells.get("Margin of safety")
    assert fv == NM_CELL, f"fair value leaked into the workbook: {fv!r}"
    assert mos == NM_CELL, f"margin of safety leaked into the workbook: {mos!r}"
    assert not isinstance(fv, (int, float)), "a number here is forwardable to a client"
    assert cells.get("Verdict") == "AVOID", "the CALL must survive; only the number goes"


def test_company_workbook_still_shows_a_clean_value():
    """The gate must not blank names the engine stands behind."""
    rec = {"intrinsic": 2954.9, "mos": 0.208, "composite": 78.0,
           "verdict": "BUY", "valuation": {}, "primary_method": "FCFF DCF"}
    cells = _summary_cells(rec)
    assert cells.get("Fair value / share (Rs)") == 2954.9
    assert cells.get("Margin of safety") == 0.208


def test_screener_row_construction_uses_the_gate():
    """Exercises the same predicate the shipped screener loop calls."""
    from app.export_routes import _num
    for gate in SUPPRESSING_GATES:
        v = _Row(gate)
        assert is_suppressed_row(v) is True
        assert (NM_CELL if is_suppressed_row(v) else _num(v.intrinsic, 2)) == NM_CELL
    assert is_suppressed_row(_Row("clean")) is False


def test_onepager_pdf_receives_no_intrinsic():
    """main.company_onepager suppresses BEFORE reading rec['intrinsic'].

    legacy.py recomputes mos = (intrinsic - price)/price, so leaking the value
    here also fabricates a margin of safety in the PDF prose.
    """
    rec = {"intrinsic": 64.36, "mos": -0.9545, "gate_state": "high_dispersion"}
    apply_value_suppression(rec, is_suppressed_rec(rec))
    assert rec.get("intrinsic") is None
    assert rec.get("mos") is None


def test_compare_serves_null_for_suppressed_rows():
    for gate in SUPPRESSING_GATES:
        v = _Row(gate)
        sup = is_suppressed_row(v)
        assert (None if sup else v.intrinsic) is None
        assert (None if sup else v.mos) is None


def test_nm_marker_is_text_not_zero_or_blank():
    """A blank cell reads as missing data; a 0 reads as a real zero valuation.
    Both are worse than saying the number was withheld."""
    assert isinstance(NM_CELL, str) and NM_CELL.strip()
    assert NM_CELL != "0" and NM_CELL != ""
    assert "meaningful" in NM_CELL.lower()
