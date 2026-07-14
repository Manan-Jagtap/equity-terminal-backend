"""
app/scorecard.py — per-stock scorecard (Tickertape-style), from data we already
compute. Every input comes from the Fund Manager v4 evidence blob (valuation
triangulation, forensic quality, momentum, growth, flow, pledge, red flags) —
so the scorecard is a synthesis, not a new model, and it stays consistent with
what the desk says about the same name.

Five sub-scores (0-100, higher = better) + an overall, plus an auto red-flag
list. Pure functions — unit-testable on an evidence dict.
"""
from __future__ import annotations


def _clamp(x, lo=2.0, hi=99.0):
    return max(lo, min(hi, x))


def _band(score):
    if score is None:
        return None
    return ("strong" if score >= 70 else "fair" if score >= 45 else "weak")


def _valuation(ev):
    tri = ev.get("tri") or {}
    band = ev.get("band") or {}
    s = tri.get("score")
    if s is None:
        # fall back to the own-history multiple percentile (cheap = high score)
        pc = band.get("combined_pct")
        return (_clamp(100 - pc) if pc is not None else None), (tri.get("suspect"))
    # blended val −1..+1 → 0..100 (undervalued = high)
    return _clamp(50 + s * 90), tri.get("suspect")


def _growth(ev):
    g = ev.get("growth")
    res = ev.get("results") or {}
    parts = []
    if g is not None:
        parts.append(_clamp(50 + g * 200))            # +25% rev → ~100
    py = res.get("pat_yoy")
    if py is not None:
        parts.append(_clamp(50 + py * 120))
    sp = res.get("surprise_pct")
    if sp is not None:
        parts.append(_clamp(55 + sp * 300))
    return round(sum(parts) / len(parts), 0) if parts else None


def _momentum(ev):
    m = ev.get("momo") or {}
    base = m.get("mom_pct")
    if base is None:
        base = 50
    adj = base
    if m.get("above_200dma") is True:
        adj += 6
    elif m.get("above_200dma") is False:
        adj -= 10
    if m.get("above_50dma") is True:
        adj += 3
    off = m.get("off_52w_high")
    if off is not None and off >= -0.05:
        adj += 5
    return round(_clamp(adj), 0)


def _quality(ev):
    q = ev.get("quality") or {}
    return round(q["composite"], 0) if q.get("composite") is not None else None


def _safety(ev):
    """Balance-sheet / governance safety: starts from accounting quality and is
    docked for pledge, forensic red flags and a suspect model."""
    q = ev.get("quality") or {}
    base = q.get("composite")
    if base is None:
        return None
    s = base
    pledged = q.get("pledge_pct")
    if pledged is not None:
        s -= min(30, pledged * 0.6)                   # 30% pledge → −18
    s -= 9 * len([f for f in (q.get("red_flags") or []) if "pledge" not in f.lower()])
    if (ev.get("tri") or {}).get("suspect"):
        s -= 5
    fl = ev.get("flow") or {}
    if (fl.get("promoter_delta") or 0) <= -1.0:
        s -= 6
    return round(_clamp(s), 0)


def red_flags(ev) -> list[str]:
    q = ev.get("quality") or {}
    out = list(q.get("red_flags") or [])              # forensic + pledge already
    fl = ev.get("flow") or {}
    if (fl.get("promoter_delta") or 0) <= -1.0:
        out.append(f"Promoter stake down {abs(fl['promoter_delta']):.1f}pp last quarter")
    ins = ev.get("insider") or {}
    if ins.get("n_sells", 0) >= 2 and (ins.get("net_qty") or 0) < 0:
        out.append(f"Insiders net sellers ({ins['n_sells']} recent disposals)")
    for nf in (ev.get("news_flags") or []):
        out.append(f"Recent news: “{nf}”")
    cc = ev.get("concall") or {}
    if cc.get("tone") is not None and cc["tone"] <= -0.3:
        out.append("Cautious tone on the latest earnings call")
    # dedupe, preserve order
    seen, dedup = set(), []
    for f in out:
        if f.lower() not in seen:
            seen.add(f.lower())
            dedup.append(f)
    return dedup


def green_flags(ev) -> list[str]:
    out = []
    q = ev.get("quality") or {}
    if (q.get("composite") or 0) >= 75 and not (q.get("red_flags")):
        out.append("Fundamentally strong — cash-backed earnings, clean flags")
    m = ev.get("momo") or {}
    if m.get("above_200dma") and (m.get("mom_pct") or 0) >= 67:
        out.append("In an uptrend with top-third momentum")
    fl = ev.get("flow") or {}
    if (fl.get("inst_delta") or 0) >= 0.5:
        out.append(f"Institutions added {fl['inst_delta']:.1f}pp last quarter")
    res = ev.get("results") or {}
    if (res.get("pat_yoy") or 0) >= 0.15:
        out.append(f"Profit up {res['pat_yoy']*100:.0f}% YoY latest quarter")
    cc = ev.get("concall") or {}
    if cc.get("tone") is not None and cc["tone"] >= 0.3:
        out.append("Confident management tone on the latest call"
                   + (f", guidance {cc['direction']}" if cc.get("direction") in ("up", "down") else ""))
    return out[:5]


# weights for the overall (valuation & quality lead; a Tickertape-like blend)
_W = {"valuation": 0.24, "quality": 0.24, "safety": 0.20, "growth": 0.18, "momentum": 0.14}


def build_scorecard(ev: dict) -> dict:
    """Map one evidence-blob entry to a scorecard. Returns available=False when
    the evidence can't support scoring."""
    if not ev:
        return {"available": False}
    val, suspect = _valuation(ev)
    scores = {
        "valuation": val,
        "quality": _quality(ev),
        "growth": _growth(ev),
        "momentum": _momentum(ev),
        "safety": _safety(ev),
    }
    present = {k: v for k, v in scores.items() if v is not None}
    overall = None
    if present:
        num = sum(_W[k] * v for k, v in present.items())
        den = sum(_W[k] for k in present)
        overall = round(num / den, 0)
    return {
        "available": bool(present),
        "overall": overall,
        "grade": ("A" if (overall or 0) >= 75 else "B" if (overall or 0) >= 60
                  else "C" if (overall or 0) >= 45 else "D") if overall is not None else None,
        "scores": {k: {"value": v, "band": _band(v)} for k, v in scores.items()},
        "valuation_suspect": bool(suspect),
        "red_flags": red_flags(ev),
        "green_flags": green_flags(ev),
        "pe_pctile_5y": (ev.get("band") or {}).get("combined_pct"),
        "alpha": ev.get("alpha"),
        "note": ("A synthesis of the terminal's own signals — valuation "
                 "triangulation, forensic quality, momentum, growth and "
                 "governance. Educational, not advice."),
    }
