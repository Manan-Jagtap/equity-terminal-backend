"""
app/results_logic.py — DB-free parsing of the latest earnings surprise from the
stored IndianAPI forecast blob, so it's unit-testable.

IndianAPI's /stock_forecasts (EPS) returns `periods[]`; reported periods carry an
`Actuals.Actual[]` block with the reported EPS, the consensus it's measured
against (SurpriseMean) and SurprisePercent (already in PERCENT units, e.g. -0.4
means -0.4%). We surface the MOST RECENT reported period (largest RelativePeriod
Number, i.e. closest to today).
"""
from __future__ import annotations


def _num(x):
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _actual_row(period):
    act = (period.get("Actuals") or {}).get("Actual")
    if isinstance(act, list) and act:
        return act[0] if isinstance(act[0], dict) else None
    if isinstance(act, dict):
        return act
    return None


def eps_surprise(forecasts: dict | None) -> dict | None:
    eps = (forecasts or {}).get("eps")
    if not isinstance(eps, dict):
        return None
    best = None
    for p in (eps.get("periods") or []):
        if not isinstance(p, dict):
            continue
        a = _actual_row(p)
        if not a:
            continue
        reported = _num(a.get("Reported"))
        if reported is None:
            continue
        rel = (p.get("RelativePeriod") or {}).get("Number")
        rel = rel if isinstance(rel, (int, float)) else -99
        cand = {
            "_rel": rel,
            "fy": (p.get("FiscalPeriod") or {}).get("Year"),
            "reported": reported,
            "estimate": _num(a.get("SurpriseMean")),
            "surprise_pct": _num(a.get("SurprisePercent")),   # already a percent
            "date": a.get("ReportedDate") or p.get("ActualReportDate"),
        }
        if best is None or cand["_rel"] > best["_rel"]:
            best = cand
    if not best:
        return None
    best.pop("_rel", None)
    # Derive surprise % if the feed didn't give one but we have estimate.
    if best["surprise_pct"] is None and best["estimate"]:
        try:
            best["surprise_pct"] = (best["reported"] / best["estimate"] - 1.0) * 100
        except ZeroDivisionError:
            pass
    if best["surprise_pct"] is not None:
        best["beat"] = best["surprise_pct"] >= 0
    return best
