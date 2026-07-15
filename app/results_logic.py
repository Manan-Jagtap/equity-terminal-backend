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


def _beat_streak(graded: list) -> int:
    """Signed run of consecutive beats (+) or misses (−) from the latest period."""
    if not graded:
        return 0
    latest = graded[-1]["beat"]
    run = 0
    for r in reversed(graded):
        if r["beat"] == latest:
            run += 1
        else:
            break
    return run if latest else -run


def eps_surprise_history(forecasts: dict | None, limit: int = 8) -> dict | None:
    """The multi-period EPS beat/miss track (oldest→newest), a beat rate, the
    current beat/miss streak, and a simple estimate-momentum read. All from the
    stored forecasts' actual-vs-estimate periods — never fabricated."""
    eps = (forecasts or {}).get("eps")
    if not isinstance(eps, dict):
        return None
    rows = []
    for p in (eps.get("periods") or []):
        if not isinstance(p, dict):
            continue
        a = _actual_row(p)
        if not a:
            continue
        reported = _num(a.get("Reported"))
        if reported is None:
            continue
        est = _num(a.get("SurpriseMean"))
        sp = _num(a.get("SurprisePercent"))
        if sp is None and est:
            try:
                sp = (reported / est - 1.0) * 100
            except ZeroDivisionError:
                sp = None
        rel = (p.get("RelativePeriod") or {}).get("Number")
        rows.append({
            "fy": (p.get("FiscalPeriod") or {}).get("Year"),
            "period": (p.get("FiscalPeriod") or {}).get("Period"),
            "date": a.get("ReportedDate") or p.get("ActualReportDate"),
            "reported": reported, "estimate": est,
            "surprise_pct": round(sp, 1) if sp is not None else None,
            "beat": (sp >= 0) if sp is not None else None,
            "_rel": rel if isinstance(rel, (int, float)) else -99,
        })
    if not rows:
        return None
    rows.sort(key=lambda r: r["_rel"])
    for r in rows:
        r.pop("_rel", None)
    rows = rows[-limit:]
    graded = [r for r in rows if r["beat"] is not None]
    beat_rate = round(sum(1 for r in graded if r["beat"]) / len(graded), 2) if graded else None
    sps = [r["surprise_pct"] for r in graded if r["surprise_pct"] is not None]
    momentum = None
    if len(sps) >= 4:
        recent, prior = sum(sps[-2:]) / 2, sum(sps[-4:-2]) / 2
        momentum = ("improving" if recent > prior + 1
                    else "deteriorating" if recent < prior - 1 else "steady")
    return {"periods": rows, "beat_rate": beat_rate, "n": len(graded),
            "streak": _beat_streak(graded), "momentum": momentum}
