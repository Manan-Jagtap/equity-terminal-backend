"""Unit tests for the transcript pipeline — the fetch guards and the
RULES-BASED summarizer (no LLM: the AI paths were stripped Jul 2026).
The network fetch itself is prod-only; everything else is pure."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")

from app.transcript_nlp import fetch_transcript_text
from app.transcript_ingester import (build_summary_md, extract_key_points,
                                     _is_noise, _clean_points)


def test_fetch_rejects_non_http_urls():
    assert fetch_transcript_text("") is None
    assert fetch_transcript_text(None) is None
    assert fetch_transcript_text("ftp://x/y.pdf") is None


def test_summary_headers_and_tone():
    pts = {"guidance": ["We expect double-digit growth in FY27."],
           "margins": [], "capex": [], "demand": [],
           "risks": ["Freight inflation remains a headwind."],
           "tone_score": 0.34, "tone_pos_hits": 10, "tone_neg_hits": 4,
           "guidance_direction": "up"}
    md = build_summary_md(pts, [{"metric": "ROE", "value": 21.0, "unit": "%"}], prior_tone=0.10)
    for h in ("## Management tone", "## Guidance & outlook",
              "## Key risks & watch-items", "## Operational metrics cited",
              "## Quarter-over-quarter shift"):
        assert h in md
    assert "confident" in md and "+0.34" in md
    assert "more confident" in md          # QoQ shift vs prior 0.10


def test_summary_without_prior_says_so():
    pts = {"guidance": [], "margins": [], "capex": [], "demand": [], "risks": [],
           "tone_score": 0.0, "tone_pos_hits": 0, "tone_neg_hits": 0,
           "guidance_direction": None}
    md = build_summary_md(pts, None, prior_tone=None)
    assert "Not enough transcript history" in md


def test_boilerplate_is_noise_and_filtered():
    assert _is_noise("thank you for joining us today to discuss the results")
    assert _is_noise("must be reviewed in conjunction with the risk factors")
    assert not _is_noise("we expect ebitda margin to expand 150 bps")
    text = ("Thank you for joining us today to discuss the Q4 results in detail. "
            "We expect double-digit revenue growth in FY27 for the company. "
            "Freight inflation is a near-term headwind we are watching closely.")
    pts = extract_key_points(text)
    assert not any("thank you" in g.lower() for g in pts["guidance"])
    assert any("double-digit" in g for g in pts["guidance"])


def test_clean_points_scrubs_stored_boilerplate():
    stored = {"guidance": ["Thank you for joining us today to discuss results.",
                           "We expect strong growth in FY27."],
              "risks": ["Demand remains soft in the north."],
              "kpis": [{"metric": "ROE", "value": 21, "unit": "%"}]}
    c = _clean_points(stored)
    assert c["guidance"] == ["We expect strong growth in FY27."]
    assert c["risks"] == stored["risks"] and c["kpis"] == stored["kpis"]
