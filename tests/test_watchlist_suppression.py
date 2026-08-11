"""A watchlist must not publish a fair value the engine withheld.

DAT-13b/DAT-15 keep the raw `intrinsic`/`mos` on the STORED Valuation row on
purpose — the batch writer, the calibration harness and the integrity sweep all
need the real number. Suppression is a presentation contract applied at the
boundary, and `app/valuation_public.py` exists because that contract "was written
down and then only half kept".

Its own docstring names the callers that must ask `is_suppressed_row()` before
showing the figure: "screener export, compare, watchlist, portfolio, backtest".
The watchlist never did. It returned the stored `intrinsic` and `mos` straight
out of the row, so a watched name whose value the engine had withdrawn still
showed a precise fair value and margin of safety — and the MoS alert fired on
that withheld number.

Run: ./venv313/bin/python -m pytest tests/test_watchlist_suppression.py -q
"""
import pytest

from app.valuation_public import (
    SUPPRESSING_GATES,
    FAIR_VALUE_NM,
    is_suppressed_row,
)
from app.watchlist_alerts import compute_alerts


class _Row:
    """Stand-in for models.Valuation — only the fields the route reads."""

    def __init__(self, gate_state, intrinsic=64.36, mos=-0.9531, verdict="AVOID"):
        self.gate_state = gate_state
        self.intrinsic = intrinsic
        self.mos = mos
        self.verdict = verdict
        self.composite = 30.0
        self.confidence = "low"
        self.analyst_target = None
        self.analyst_upside = None
        self.pe = self.pb = self.roe = None


def _payload(row):
    """The suppression half of _enrich(), isolated from the DB."""
    suppressed = is_suppressed_row(row)
    mos = None if suppressed else (row.mos if row else None)
    intrinsic = None if suppressed else (row.intrinsic if row else None)
    out = {"intrinsic": intrinsic, "mos": mos, "verdict": row.verdict if row else None}
    if suppressed:
        out["value_suppressed"] = True
        out["fair_value_note"] = FAIR_VALUE_NM
    return out


@pytest.mark.parametrize("gate", sorted(SUPPRESSING_GATES))
def test_every_suppressing_gate_withholds_the_number(gate):
    """Parametrised over the CONTRACT, so a new suppressing gate is covered
    the moment it is added to SUPPRESSING_GATES rather than silently leaking."""
    out = _payload(_Row(gate))
    assert out["intrinsic"] is None, f"{gate} leaked an intrinsic"
    assert out["mos"] is None, f"{gate} leaked a margin of safety"
    assert out["value_suppressed"] is True
    assert out["fair_value_note"] == FAIR_VALUE_NM
    # the verdict SURVIVES — the engine keeps its call, it withholds the level
    assert out["verdict"] == "AVOID"


def test_a_clean_row_still_publishes_its_numbers():
    out = _payload(_Row("clean", intrinsic=2729.0, mos=0.1197, verdict="ACCUMULATE"))
    assert out["intrinsic"] == 2729.0
    assert out["mos"] == 0.1197
    assert "value_suppressed" not in out


def test_a_missing_row_is_not_treated_as_suppressed():
    assert is_suppressed_row(None) is False


def test_the_mos_alert_cannot_fire_on_a_withheld_number():
    """The alert reads `cur["mos"]`. Before the fix that was the raw stored
    figure, so a user got 'margin of safety crossed 15%' on a name the engine
    had refused to value."""
    cfg = {"alert_verdict": 0, "alert_mos": 1, "alert_target": 0, "alert_move": 0,
           "mos_threshold": 0.15, "move_threshold": 0.08, "target_price": None,
           "last_verdict": "AVOID"}
    row = _Row("value_suppressed", mos=0.95)          # a huge, withheld MoS
    out = _payload(row)
    alerts = compute_alerts(cfg, {"verdict": out["verdict"], "mos": out["mos"],
                                  "price": 100.0, "day_move": 0.0})
    assert not [a for a in alerts if a["type"] == "mos"], \
        "an alert fired on a fair value the engine withdrew"

    # control: the same threshold DOES fire on a clean row, so the assertion
    # above is proving suppression rather than a dead alert path.
    clean = _payload(_Row("clean", mos=0.95))
    fired = compute_alerts(cfg, {"verdict": clean["verdict"], "mos": clean["mos"],
                                 "price": 100.0, "day_move": 0.0})
    assert [a for a in fired if a["type"] == "mos"], "control failed: mos alert never fires"
