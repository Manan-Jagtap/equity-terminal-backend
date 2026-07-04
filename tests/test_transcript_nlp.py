"""Unit tests for the transcript summarizer — prompt grounding + the
no-fabrication fallbacks. The network fetch + Claude call are prod-only."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")
os.environ.pop("ANTHROPIC_API_KEY", None)   # ensure the no-key path is testable

from app.transcript_nlp import build_prompt, summarize_transcript, fetch_transcript_text


def test_build_prompt_grounding_and_headers():
    sysp, user = build_prompt("Tata Consultancy", "TCS", "Q4 FY26", "revenue grew this quarter")
    assert "ONLY the transcript text" in sysp
    for h in ("## Guidance & Outlook", "## Management Tone", "## Key Risks", "## Quarter-over-quarter shift"):
        assert h in user
    assert "Not enough transcript history" in user      # no prior transcript


def test_build_prompt_qoq_when_prior_present():
    _, user = build_prompt("X", "X", "Q1", "this quarter", prev_text="last quarter")
    assert "shifted versus the prior-quarter" in user


def test_no_concalls_returns_unavailable_not_a_summary():
    r = summarize_transcript("X Ltd", "X", [], api_key="k")
    assert r["available"] is False and "No concall" in r["message"]
    assert "summary" not in r


def test_no_api_key_returns_unavailable():
    r = summarize_transcript("X Ltd", "X", [{"transcript": "http://a/b.pdf", "date": "Q1"}], api_key="")
    assert r["available"] is False and "ANTHROPIC" in r["message"]


def test_fetch_rejects_non_http_urls():
    assert fetch_transcript_text("") is None
    assert fetch_transcript_text(None) is None
    assert fetch_transcript_text("ftp://x/y.pdf") is None
