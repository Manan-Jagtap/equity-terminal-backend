"""
app/consensus.py — extract analyst consensus from a CompanyInsight blob.

Kept deliberately separate from the valuation engine: the analyst view is
displayed ALONGSIDE the independent model, never blended into the intrinsic.
"""
from __future__ import annotations


def _f(x):
    try:
        v = float(x)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def analyst_consensus(insight_data: dict | None, price: float | None) -> dict | None:
    """Return {target, low, high, rating, n, upside} or None.
    `insight_data` is the CompanyInsight.data JSON blob."""
    if not insight_data:
        return None
    tgt = (insight_data.get("target") or {})
    target = _f(tgt.get("mean")) or _f(tgt.get("median"))
    rating = ((insight_data.get("analyst") or {}).get("rating")) or None
    if target is None and rating is None:
        return None
    upside = None
    if target and price and price > 0:
        upside = (target - price) / price
    return {
        "target": target,
        "low": _f(tgt.get("low")),
        "high": _f(tgt.get("high")),
        "n": tgt.get("n_estimates"),
        "rating": rating,
        "upside": upside,
    }
