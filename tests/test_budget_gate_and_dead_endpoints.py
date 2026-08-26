"""Two holes the 26 Aug 2026 insight repair walked straight through.

1. The vendor budget was PRE-FLIGHTED at batch entry points and never checked at
   the call site, so ~1,600 _get_safe calls took the month from under budget to
   9,567 of 9,500 with nothing objecting. FIX-07 made those calls counted; it
   never made them gated.

2. Nothing separated "the vendor answered" from "the endpoint served data". For
   a month /historical_stats replied to every name with 200 {"info": ...} while
   health read a clean ok and 633 insight rows were poisoned.
"""
import pytest

from app import api_budget as B
from app import vendor_meter as vm
import app.ingest.indianapi_ingester as ing


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    B._reset_ceiling_cache()
    vm._reset_envelopes()
    monkeypatch.delenv("INDIANAPI_ALLOW_OVER", raising=False)
    yield
    B._reset_ceiling_cache()
    vm._reset_envelopes()


# ── 1. the budget ceiling now reaches the call site ─────────────────────────

def test_get_safe_returns_none_when_out_of_budget(monkeypatch):
    """Absent, not an error body — so the DATA-12 merge keeps the stored value."""
    monkeypatch.setattr(B, "over_budget", lambda force=False: True)
    called = []
    monkeypatch.setattr(ing.requests, "get", lambda *a, **k: called.append(1))
    assert ing._get_safe("/historical_stats", {"stock_name": "X"}) is None
    assert called == [], "a refused call must not reach the network"


def test_get_safe_costs_no_quota_when_refused(monkeypatch):
    """Refused BEFORE the tick — a call that cannot happen must not be billed."""
    monkeypatch.setattr(B, "over_budget", lambda force=False: True)
    monkeypatch.setattr(ing.requests, "get", lambda *a, **k: None)
    before = vm.total()
    ing._get_safe("/historical_stats", {"stock_name": "X"})
    assert vm.total() == before


def test_get_raises_rather_than_returning_nothing(monkeypatch):
    """_get feeds REQUIRED fields whose callers store what they get. A silent
    None here would be written over good data exactly like the DATA-12 envelope
    was, so running dry must be loud."""
    monkeypatch.setattr(B, "over_budget", lambda force=False: True)
    monkeypatch.setattr(ing, "KEY", "realkey123")
    with pytest.raises(ing.VendorBudgetExhausted):
        ing._get("/stock", {"name": "X"})


def test_override_is_the_same_one_the_preflight_uses(monkeypatch):
    """One documented way past the ceiling, not two that can disagree."""
    monkeypatch.setattr(B, "over_budget", lambda force=False: True)
    monkeypatch.setenv("INDIANAPI_ALLOW_OVER", "1")
    assert ing._budget_blocked() is False


def test_budget_check_fails_open(monkeypatch):
    """A metering fault must not halt ingest — that was never the hole."""
    def boom(force=False):
        raise RuntimeError("db down")
    monkeypatch.setattr(B, "over_budget", boom)
    assert ing._budget_blocked() is False


def test_over_budget_is_cached(monkeypatch):
    """It sits in front of every call, so it must not query per call."""
    calls = []
    monkeypatch.setattr(B, "remaining", lambda db, month=None: calls.append(1) or 0)
    B._reset_ceiling_cache()
    assert B.over_budget() is True
    for _ in range(20):
        B.over_budget()
    assert len(calls) == 1, f"expected one DB read, got {len(calls)}"


# ── 2. an endpoint serving only envelopes is visible ────────────────────────

def test_data12_shape_is_flagged_as_a_dead_endpoint():
    """The exact body that hid for a month: 200 {"info": "Not a valid ..."}."""
    for _ in range(10):
        vm.note_payload("/historical_stats", {"info": "Not a valid script_code"})
    dead = vm.envelope_endpoints()
    assert [d["path"] for d in dead] == ["/historical_stats"]
    assert dead[0]["envelope_pct"] == 1.0


def test_uptime_semantics_are_untouched():
    """payload_ok still scores {"info": ...} a success on purpose. The new
    counter must ADD a view, not quietly redefine "the vendor is down"."""
    assert vm.payload_ok({"info": "Not a valid script_code"}) is True
    assert vm._is_envelope({"info": "Not a valid script_code"}) is True


def test_a_healthy_endpoint_is_not_flagged():
    for _ in range(20):
        vm.note_payload("/stock", {"companyName": "Reliance"})
    assert vm.envelope_endpoints() == []


def test_a_thin_sample_is_never_flagged():
    """A quiet endpoint called three times must not look broken."""
    for _ in range(3):
        vm.note_payload("/rare", {"error": "nope"})
    assert vm.envelope_endpoints() == []


def test_transport_failure_is_not_an_envelope():
    """None means no answer, which says nothing about what the endpoint serves —
    and it is already counted as a plain failure elsewhere."""
    for _ in range(20):
        vm.note_payload("/down", None)
    assert vm.envelope_summary()["tracked"] == 0


def test_a_recovering_endpoint_clears():
    """The ring must not latch: today's outage recovered within minutes."""
    for _ in range(20):
        vm.note_payload("/historical_stats", {"error": "boom"})
    assert vm.envelope_endpoints(), "should be flagged while broken"
    for _ in range(20):
        vm.note_payload("/historical_stats", {"Debtor Days": {"Mar 2025": 16.0}})
    assert vm.envelope_endpoints() == [], "should clear once data returns"


def test_endpoints_are_tracked_independently():
    for _ in range(10):
        vm.note_payload("/historical_stats", {"info": "x"})
        vm.note_payload("/stock", {"companyName": "Y"})
    assert [d["path"] for d in vm.envelope_endpoints()] == ["/historical_stats"]
