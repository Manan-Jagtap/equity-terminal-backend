"""app/valuation_public.py — the ONE place a withheld fair value is stripped.

DAT-13b/DAT-15 keep the raw `intrinsic`/`mos` on the engine result and on the
stored Valuation row on purpose: the batch writer, the calibration harness and
the integrity sweep all need the real number. Suppression is a PRESENTATION
contract applied at the boundary.

This module exists because that contract was written down and then only half
kept. The helper lived inside `main.py`, so only `main.py` could reach it —
`export_routes.py`, the one-pager PDF and `compare_routes.py` each read
`intrinsic`/`mos` straight off the recommendation or the DB row and published
them.

Measured live on 2026-08-04: 111 of 997 names carried `value_suppressed=true`
with `intrinsic: null` in the JSON API, while `GET /api/export/ADANIGREEN.xlsx`
printed "Fair value / share (Rs) 64.36" and "Margin of safety -0.9545" in its
Summary sheet. ADANIGREEN is the exact name the DAT-13 comment in engines.py
cites as the reason the gate exists. The website said "n/m"; the workbook a user
forwards to someone else carried a precise number the engine had formally
withdrawn.

SPEC_DAT13_SUPPRESS_VALUE.md already required this ("One-pager PDF / Excel: same
n/m treatment — no cell may carry the raw figure") and named the failure mode in
its Risks section ("any surface that forgets the flag will print the number
again"). It forgot on three surfaces at once, which is the argument for a shared
module rather than a fourth hand-written call site.

Import from here, never re-implement.
"""
from __future__ import annotations

# Gates meaning "the engine withheld its point estimate". Any gate that sets
# value_suppressed=True in engines.recommend() MUST appear here, or a surface
# reading the PERSISTED gate_state keeps publishing what the live path withdrew.
# tests/test_dat15_dispersion_abstention.py::test_suppressing_gates_contract
# pins this against engines.py.
SUPPRESSING_GATES = frozenset({"value_suppressed", "high_dispersion"})
VALUE_SUPPRESSED_GATE = "value_suppressed"
FAIR_VALUE_NM = "not meaningful"

# What a spreadsheet cell or PDF field shows in place of the number. A string,
# never 0 and never blank: a blank cell reads as "missing data" and a 0 reads as
# a real valuation of zero. Both are worse than saying so.
NM_CELL = "n/m — not meaningful"


def is_suppressed_rec(rec) -> bool:
    """True when a LIVE engine result withheld its point estimate."""
    if not rec:
        return False
    if rec.get("value_suppressed"):
        return True
    return rec.get("gate_state") in SUPPRESSING_GATES


def is_suppressed_row(row) -> bool:
    """True when a STORED Valuation row was written under a suppressing gate.

    The stored row keeps the raw figure by design, so callers reading the DB
    directly (screener export, compare, watchlist, portfolio, backtest) must ask
    this before showing `intrinsic`/`mos`.
    """
    if row is None:
        return False
    gate = getattr(row, "gate_state", None)
    if gate is None and isinstance(row, dict):
        gate = row.get("gate_state")
    return gate in SUPPRESSING_GATES


def apply_value_suppression(payload: dict, suppressed) -> dict:
    """Null the point estimate, keep the verdict. Idempotent; safe on any dict."""
    if not suppressed:
        return payload
    for k in ("intrinsic", "mos", "blended"):
        if k in payload:
            payload[k] = None
    payload["value_suppressed"] = True
    payload["fair_value_note"] = FAIR_VALUE_NM
    return payload


def cell(value, suppressed):
    """A spreadsheet/PDF cell: the value, or the n/m marker when withheld."""
    return NM_CELL if suppressed else value
