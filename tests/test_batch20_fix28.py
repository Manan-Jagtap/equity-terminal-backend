"""Batch 20 — FIX-28 hygiene/product tail (core cluster): the LTCG boundary
(ENG-10), the sentiment negation/expiry/ambiguous-token fixes (ENG-13), and
proof the probe/dump/fabricated-facts litter is gone from the package (ARCH-02/
DATA-07). The exemption double-count (ENG-11) is a code fix in manager_report's
after-tax section, verified by inspection."""
import datetime as dt
import os
from app.portfolio_routes import _term_fields
from app.sentiment import news_headline_score, _news_one, _BULL_SINGLES, _BEAR_SINGLES

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _H:
    def __init__(self, days_ago):
        self.buy_date = dt.date.today() - dt.timedelta(days=days_ago)
        self.added_at = None


# ── ENG-10: LTCG needs MORE than 12 months ────────────────────────────────────

def test_ltcg_boundary_365_is_short_366_is_long():
    assert _term_fields(_H(365))["term"] == "short"   # exactly 12 months → still short
    assert _term_fields(_H(365))["days_to_lt"] == 1    # one more day to go
    assert _term_fields(_H(366))["term"] == "long"     # long-term begins day 366
    assert _term_fields(_H(366))["days_to_lt"] == 0
    assert _term_fields(_H(100))["days_to_lt"] == 266   # 366 - 100


# ── ENG-13: negation, ambiguous tokens dropped, stale-news expiry ─────────────

def test_negation_not_counted_bearish():
    s_neg, _ = news_headline_score(["Company denies fraud; no wrongdoing found"])
    # "fraud"/"wrongdoing" negated → nothing scores bearish
    assert s_neg is None or s_neg >= 0
    s_pos, _ = news_headline_score(["Company hit by fraud probe"])   # un-negated
    assert s_pos is not None and s_pos < 0


def test_ambiguous_tokens_dropped():
    # "high" and "record" no longer count bullish; "cut"/"fine" no longer bearish
    for w in ("high", "record", "cut", "cuts", "fine"):
        assert w not in _BULL_SINGLES and w not in _BEAR_SINGLES
    # "high debt" is now neutral, not bullish
    assert news_headline_score(["Company carries high debt"])[0] in (None, 0.0)


def test_stale_news_expires(monkeypatch):
    import app.sentiment as S
    fresh = dt.date.today().isoformat()
    stale = (dt.date.today() - dt.timedelta(days=40)).isoformat()

    def fake_kv(db, key):
        return {"AAA": {"score": -0.8, "as_of": stale},
                "BBB": {"score": 0.5, "as_of": fresh}}
    monkeypatch.setattr("app.manager_engine._kv_get", fake_kv)
    assert _news_one(None, "AAA") is None    # >30d → expired
    assert _news_one(None, "BBB") == 0.5      # fresh → kept


# ── ARCH-02 / DATA-07: litter gone from the package ───────────────────────────

def test_no_probe_or_dump_litter_in_package():
    ing = os.path.join(ROOT, "app", "ingest")
    litter = [f for f in os.listdir(ing)
              if f.startswith("probe_") or f.endswith("_dump.json") or f == "endpoint_probe.py"]
    assert litter == [], f"probe/dump litter still present: {litter}"


def test_dead_fabricated_facts_ingester_removed():
    assert not os.path.exists(os.path.join(ROOT, "app", "bse_results_ingester.py"))
    assert not os.path.exists(os.path.join(ROOT, "app", "ingest", "bse_results_ingester.py"))
