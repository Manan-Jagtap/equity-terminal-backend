"""Two integrity-sweep false positives that turned the live sweep RED on
2026-07-26 with no user-visible defect behind either.

  * LICI     — verdict "LOW CONF", confidence "medium", MoS +565%. The product
               correctly showed a NO-CALL, but the check only inspected
               `confidence`, so it reported the name as UNGATED (P1).
  * CCAVENUE — a never-onboarded stub (shares 0, price 0, verdict NO DATA) was
               flagged P1 "price_absurd" although nothing is shown to the user.

Severity must track USER-VISIBLE harm: a gated name is gated whichever field
carries the gate, and an un-onboarded stub is a coverage gap, not corruption.
"""
import os, sys, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")

import inspect
from app import data_integrity


def _src():
    return inspect.getsource(data_integrity)


def test_absurd_mos_is_gated_by_verdict_not_only_confidence():
    """A LOW CONF verdict must count as gated even when confidence != 'low'."""
    src = _src()
    assert '_gated = conf in ("low",) or verdict in ("LOW CONF", "NO CALL", "NO DATA")' in src, \
        "the MoS gate must honour the verdict, not just the confidence field"
    assert 'abs(v.mos) > 5 and not _gated' in src
    # The old, confidence-only form must be gone.
    assert 'abs(v.mos) > 5 and conf not in ("low",)' not in src


def test_confident_absurd_mos_still_flags_p1():
    """The check must NOT be weakened: an absurd MoS on a COMMITTED verdict
    (e.g. BUY at medium confidence) is still a real P1."""
    src = _src()
    # The P1 flag still exists and is still reached when not gated.
    assert '_flag(findings, tk, "mos_absurd_ungated", "P1", v.mos)' in src


def test_unonboarded_stub_price_is_p2_not_p1():
    """A stub with no shares shows the user nothing → coverage gap (P2)."""
    src = _src()
    assert '_onboarded = bool(co.shares_outstanding and co.shares_outstanding > 0)' in src
    assert '"price_absent_not_onboarded", "P2"' in src
    # An ONBOARDED name with a broken price is still P1 (the real corruption).
    assert 'if _onboarded:' in src
    assert '_flag(findings, tk, "price_absurd", "P1", m.price)' in src


def test_sweep_still_runs_end_to_end():
    """Guard against a syntax/flow break in the edited function."""
    from app.database import SessionLocal, engine, Base
    from app import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    fn = getattr(data_integrity, "store_sweep", None) or getattr(data_integrity, "sweep", None)
    assert fn is not None, "sweep entrypoint missing"
    out = fn(db)
    assert isinstance(out, dict) and "status" in out
    db.close()
