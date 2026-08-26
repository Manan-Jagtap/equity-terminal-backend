"""A delisted security must not be presented as a live one.

JBCHEPHARM was amalgamated into Torrent Pharmaceuticals on 8 Jul 2026 and left
both data vendors, yet the terminal kept serving its last quote (Rs 2,408.9,
28 days old) with confidence 1.0 "high" and live momentum commentary. The
valuation gate did abstain, so no buy/sell call was made — but price, confidence
and momentum all described a security that no longer exists.

These tests lock the three parts of the fix: the registry knows, the trust layer
reacts, and the sweep would catch the NEXT one without anyone noticing by hand.
"""
import datetime as _dt
import pytest

from app.corporate_events import for_ticker, verified_vendor_alias
from app.data_quality import data_quality, STALE_PRICE_DAYS


def _co(**over):
    base = dict(type="nonfinancial", equity=100.0, shares=10.0,
                net_profit=20.0, price=50.0, revenue=200.0, net_debt=0.0)
    base.update(over)
    return base


# ── registry ────────────────────────────────────────────────────────────────

def test_jbchepharm_registered_as_delisted():
    ev = for_ticker("JBCHEPHARM")
    assert ev is not None, "the amalgamation must be registered"
    assert ev["delisted"] is True
    assert ev["effective"] == "2026-07-08"
    assert ev["last_traded"] == "2026-07-15"


def test_jbchepharm_never_aliases_to_torrent():
    """TORNTPHARM is a SEPARATE security with its own listing and history.

    Aliasing it here would attach Torrent's financials to our JBCHEPHARM ticker
    — the DATA-01 / VAML-VISL contamination shape. This is the single most
    dangerous edit someone could make to that entry, so it is pinned.
    """
    assert verified_vendor_alias("JBCHEPHARM") is None


def test_a_live_corporate_event_is_not_marked_delisted():
    """GUJENERGY had a corporate action but still trades — the two states must
    stay distinguishable, or the banner would suppress a live price."""
    ev = for_ticker("GUJENERGY")
    assert ev is not None
    assert ev.get("delisted") is not True


# ── trust layer ─────────────────────────────────────────────────────────────

def test_delisted_drops_confidence_below_reliable():
    """Below 0.5 is what makes the verdict read LOW CONF rather than a number."""
    q = data_quality(_co(delisted=True, price_stale_days=28))
    assert q["score"] < 0.5, q
    assert q["level"] == "low"
    assert any("no longer listed" in f.lower() for f in q["flags"])


def test_stale_price_is_penalised_but_less_than_delisting():
    """Old and gone are different states and must not score the same."""
    stale = data_quality(_co(price_stale_days=28))
    dead = data_quality(_co(delisted=True, price_stale_days=28))
    fresh = data_quality(_co(price_stale_days=1))
    assert fresh["score"] > stale["score"] > dead["score"]
    assert fresh["score"] == pytest.approx(1.0)


def test_stale_threshold_matches_the_integrity_sweep():
    """One definition of "stale", or the sweep and the badge contradict."""
    from app.data_integrity import STALE_PRICE_DAYS as SWEEP_DAYS
    assert STALE_PRICE_DAYS == SWEEP_DAYS
    assert data_quality(_co(price_stale_days=STALE_PRICE_DAYS))["score"] == pytest.approx(1.0)
    assert data_quality(_co(price_stale_days=STALE_PRICE_DAYS + 1))["score"] < 1.0


def test_absent_fields_score_exactly_as_before():
    """The parity fixtures carry neither field. If their presence-by-default
    changed a score, every committed verdict case would shift underneath us."""
    assert data_quality(_co())["score"] == pytest.approx(1.0)
    assert data_quality(_co())["flags"] == []


# ── the sweep's leading indicator ───────────────────────────────────────────

class _Co:
    def __init__(self, id, ticker): self.id, self.ticker = id, ticker


class _Snap:
    def __init__(self, price): self.price = price


def _universe(missing, n=200):
    """A realistic universe: `missing` absent among n names that resolve.

    Size matters here — the guard decides on the PROPORTION absent, so a
    one-name universe is indistinguishable from a broken master and is
    correctly ignored. Real delistings are a handful in ~1,000.
    """
    cos = [_Co(i, f"OK{i}") for i in range(n)]
    cos += [_Co(1000 + j, t) for j, t in enumerate(missing)]
    snaps = {c.id: _Snap(2408.9) for c in cos}
    return cos, snaps


def test_sweep_flags_a_vanished_ticker_that_still_publishes(monkeypatch):
    from app import data_integrity as di
    import app.dhan.instruments as ins
    monkeypatch.setattr(ins, "security_id",
                        lambda t, index=False: None if t == "GONE" else "123")
    cos, snaps = _universe(["GONE"])
    findings = []
    di._listing_findings(findings, cos, snaps)
    assert len(findings) == 1
    assert findings[0]["ticker"] == "GONE"
    assert findings[0]["check"] == "delisted_still_publishing"
    assert findings[0]["severity"] == "P1"


def test_sweep_grades_a_vanished_ticker_that_publishes_nothing_lower(monkeypatch):
    """DUMMYINXGN-style placeholder scrips: absent, but harmless — they serve no
    price, so nobody can act on them. Worth knowing, not worth going red."""
    from app import data_integrity as di
    import app.dhan.instruments as ins
    monkeypatch.setattr(ins, "security_id",
                        lambda t, index=False: None if t == "QUIET" else "123")
    cos, snaps = _universe(["QUIET"])
    snaps[[c for c in cos if c.ticker == "QUIET"][0].id] = _Snap(None)
    findings = []
    di._listing_findings(findings, cos, snaps)
    assert len(findings) == 1
    assert findings[0]["check"] == "absent_from_instrument_master"
    assert findings[0]["severity"] == "P2"


def test_sweep_stays_silent_on_an_acknowledged_delisting(monkeypatch):
    """JBCHEPHARM is handled; re-flagging it would hold integrity red forever."""
    from app import data_integrity as di
    import app.dhan.instruments as ins
    monkeypatch.setattr(ins, "security_id",
                        lambda t, index=False: None if t == "JBCHEPHARM" else "123")
    cos, snaps = _universe(["JBCHEPHARM"])
    findings = []
    di._listing_findings(findings, cos, snaps)
    assert findings == []


def test_sweep_goes_quiet_when_too_much_of_the_universe_is_absent(monkeypatch):
    """The threshold itself: past it, the master is not describing our universe
    and no single absence within it can be trusted."""
    from app import data_integrity as di
    import app.dhan.instruments as ins
    gone = {f"G{i}" for i in range(20)}          # 20 of 120 = 16.7%, well over 2%
    monkeypatch.setattr(ins, "security_id",
                        lambda t, index=False: None if t in gone else "123")
    cos, snaps = _universe(sorted(gone), n=100)
    findings = []
    di._listing_findings(findings, cos, snaps)
    assert findings == []


def test_sweep_flags_nothing_when_the_master_resolves_nothing(monkeypatch):
    """The failure that matters: a master that loaded empty must not condemn all
    ~1,000 names and bury a real finding under noise."""
    from app import data_integrity as di
    import app.dhan.instruments as ins
    monkeypatch.setattr(ins, "security_id", lambda t, index=False: None)
    findings = []
    di._listing_findings(findings, [_Co(i, f"T{i}") for i in range(50)],
                         {i: _Snap(100.0) for i in range(50)})
    assert findings == [], "100% absent proves nothing about any one name"


def test_sweep_survives_a_raising_master(monkeypatch):
    from app import data_integrity as di
    import app.dhan.instruments as ins
    def boom(t, index=False): raise RuntimeError("network down")
    monkeypatch.setattr(ins, "security_id", boom)
    findings = []
    di._listing_findings(findings, [_Co(1, "ANY")], {1: _Snap(100.0)})
    assert findings == []
