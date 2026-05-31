"""
app/bse/classifier.py — Classify a BSE announcement into a doc_type.

Doc types we care about:
    IP            — Investor Presentation
    TRANSCRIPT    — Earnings/concall transcript
    PRESS_RELEASE — Press release (Q-results commentary)
    FINANCIAL     — Audited/unaudited financial results
    OTHER         — Everything else (corp governance, board meetings, etc.)

The classifier uses (in order):
    1. Subcategory match  — BSE's SUBCATNAME is the most reliable signal
    2. Headline keyword match  — fallback when SUBCATNAME is blank/generic
"""
from __future__ import annotations
from enum import Enum
from dataclasses import dataclass
from typing import Optional


class DocType(str, Enum):
    IP = "IP"
    TRANSCRIPT = "TRANSCRIPT"
    PRESS_RELEASE = "PRESS_RELEASE"
    FINANCIAL = "FINANCIAL"
    OTHER = "OTHER"


# Order matters — most specific first. Matched as substrings, case-insensitive.
_SUBCAT_RULES: list[tuple[str, DocType]] = [
    ("investor presentation", DocType.IP),
    ("investor update", DocType.IP),
    ("investor meet", DocType.IP),
    ("earnings call transcript", DocType.TRANSCRIPT),
    ("concall transcript", DocType.TRANSCRIPT),
    ("conference call transcript", DocType.TRANSCRIPT),
    ("transcript", DocType.TRANSCRIPT),
    ("press release", DocType.PRESS_RELEASE),
    ("media release", DocType.PRESS_RELEASE),
    ("financial results", DocType.FINANCIAL),
    ("audited financial", DocType.FINANCIAL),
    ("unaudited financial", DocType.FINANCIAL),
    ("quarterly results", DocType.FINANCIAL),
]

_HEADLINE_RULES: list[tuple[str, DocType]] = [
    # "transcript" first: many transcripts are titled e.g.
    # "Transcript of conference call ... financial results ..." and must not
    # fall through to the FINANCIAL rule below.
    ("transcript", DocType.TRANSCRIPT),
    ("investor presentation", DocType.IP),
    ("investor update", DocType.IP),
    ("results presentation", DocType.IP),
    ("earnings presentation", DocType.IP),
    ("earnings call presentation", DocType.IP),
    ("q4 presentation", DocType.IP),
    ("q3 presentation", DocType.IP),
    ("q2 presentation", DocType.IP),
    ("q1 presentation", DocType.IP),
    ("press release", DocType.PRESS_RELEASE),
    ("media release", DocType.PRESS_RELEASE),
    ("financial results", DocType.FINANCIAL),
    ("audited financial", DocType.FINANCIAL),
    ("unaudited financial", DocType.FINANCIAL),
]


@dataclass
class Classification:
    doc_type: DocType
    matched_on: str   # "subcat" | "headline" | "default"
    matched_rule: Optional[str] = None


def classify_announcement(headline: str, subcategory: str = "") -> Classification:
    """
    Classify a BSE announcement.

    Args:
        headline: the HEADLINE field
        subcategory: the SUBCATNAME field (preferred signal if present)

    Returns:
        Classification with doc_type and provenance info.
    """
    sub = (subcategory or "").lower().strip()
    head = (headline or "").lower().strip()

    # 1. Subcategory match
    for keyword, dt in _SUBCAT_RULES:
        if keyword in sub:
            return Classification(doc_type=dt, matched_on="subcat", matched_rule=keyword)

    # 2. Headline keyword match
    for keyword, dt in _HEADLINE_RULES:
        if keyword in head:
            return Classification(doc_type=dt, matched_on="headline", matched_rule=keyword)

    return Classification(doc_type=DocType.OTHER, matched_on="default")


# --- self test ---------------------------------------------------------------

_TEST_CASES = [
    # (headline, subcat, expected)
    ("Investor Presentation - Q4 FY26", "", DocType.IP),
    ("", "Investor Presentation", DocType.IP),
    ("Earnings Call Transcript - Q4 FY26", "", DocType.TRANSCRIPT),
    ("Concall transcript for the quarter ended March 2026", "", DocType.TRANSCRIPT),
    ("Press Release on Q4 Results", "", DocType.PRESS_RELEASE),
    ("Audited Financial Results for FY26", "", DocType.FINANCIAL),
    ("Board Meeting Notification", "", DocType.OTHER),
    ("Allotment of Equity Shares", "", DocType.OTHER),
    ("Q4FY26 Results Presentation", "", DocType.IP),
    ("", "Earnings Call Transcript", DocType.TRANSCRIPT),
]


if __name__ == "__main__":
    passed = 0
    for head, sub, expected in _TEST_CASES:
        got = classify_announcement(head, sub).doc_type
        status = "✓" if got == expected else "✗"
        print(f"{status} headline={head!r:60} sub={sub!r:30} → {got.value} (expected {expected.value})")
        if got == expected:
            passed += 1
    print(f"\n{passed}/{len(_TEST_CASES)} passed.")
