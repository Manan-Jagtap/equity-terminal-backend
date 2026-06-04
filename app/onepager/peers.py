"""
app/onepager/peers.py — build the peer-comparison block for a one-pager from
the canonical peer_metrics of several extracted companies.
"""
from __future__ import annotations

# (canonical key, column header) in display order
_COLS = [
    ("aum", "AUM (Rs cr)"),
    ("aum_growth_yoy", "AUM gr. YoY"),
    ("nim", "NIM"),
    ("roa", "RoA"),
    ("roe", "RoE"),
    ("gs3", "GS-III"),
    ("cof", "CoF"),
]


def build_peer_block(subject_ticker: str, extractions: dict) -> dict | None:
    """
    extractions: {ticker: extracted_dict}  (each must carry company + peer_metrics)
    Returns a peers block for render, with the subject row flagged self=True.
    """
    if len(extractions) < 2:
        return None

    rows = []
    for ticker, data in extractions.items():
        pm = data.get("peer_metrics") or {}
        cells = []
        for key, _ in _COLS:
            v = pm.get(key)
            cells.append(v if v not in (None, "", "null") else "\u2014")
        rows.append({
            "name": data["company"]["name"],
            "self": ticker.upper() == subject_ticker.upper(),
            "cells": cells,
            "_ticker": ticker.upper(),
        })

    # subject first, then the rest by AUM-ish ordering (keep input order otherwise)
    rows.sort(key=lambda r: (not r["self"], r["_ticker"]))
    for r in rows:
        r.pop("_ticker", None)

    return {
        "columns": [h for _, h in _COLS],
        "rows": rows,
        "note": "Peer figures from each company's latest investor presentation / "
                "transcript; gold-loan vs diversified models are not strictly "
                "like-for-like. Verify against filings before use.",
    }
