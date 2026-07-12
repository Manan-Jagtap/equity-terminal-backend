"""
app/ownership_logic.py — DB-free parsing of a shareholding snapshot from the raw
IndianAPI /stock payload, so it's unit-testable and reusable by the ingester.

Buckets the (sector-varying) shareholding categories into promoter / FII / DII /
mutual-fund / public, takes the latest reported quarter and the quarter before it,
and returns each bucket's percentage + QoQ delta. `institutional` = FII + DII.
"""
from __future__ import annotations
import re

_MON = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def _num(x):
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _datekey(d):
    s = str(d or "").strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    parts = s.split()
    if len(parts) == 2:
        try:
            return (int(parts[1]), _MON.get(parts[0][:3].lower(), 0), 0)
        except (ValueError, TypeError):
            pass
    return (0, 0, 0)


def _classify(name):
    n = (name or "").lower()
    if "promoter" in n:
        return "promoter"
    if "mutual" in n or n.strip() == "mf":
        return "mf"
    if "foreign" in n or "fii" in n or "fpi" in n:
        return "fii"
    if "domestic" in n or "dii" in n or "insurance" in n or "financial institution" in n:
        return "dii"
    if "public" in n or "retail" in n or "non institution" in n or "non-institution" in n:
        return "public"
    if "government" in n or "president of india" in n or n.strip() == "goi":
        return "government"
    # Keep everything the vendor reports — unmatched categories (e.g. the
    # explicit "Other" bucket some names carry) must not silently vanish.
    return "other"


def ownership_snapshot(stock: dict | None) -> dict | None:
    cats = (stock or {}).get("shareholding") if isinstance(stock, dict) else None
    if not isinstance(cats, list):
        return None

    bucket_dates: dict = {}      # bucket -> {date: summed pct}
    dates, seen = [], set()
    for cat in cats:
        if not isinstance(cat, dict):
            continue
        b = _classify(cat.get("displayName") or cat.get("categoryName"))
        if not b:
            continue
        for e in (cat.get("categories") or []):
            if not isinstance(e, dict):
                continue
            d, p = e.get("holdingDate"), _num(e.get("percentage"))
            if not d or p is None:
                continue
            if d not in seen:
                seen.add(d); dates.append(d)
            bd = bucket_dates.setdefault(b, {})
            bd[d] = (bd.get(d) or 0) + p     # sum sub-categories into the bucket

    if not dates:
        return None
    dates.sort(key=_datekey)
    latest = dates[-1]
    prev = dates[-2] if len(dates) >= 2 else None

    out = {"as_of": latest}
    for b in ("promoter", "fii", "dii", "mf", "public", "government", "other"):
        bd = bucket_dates.get(b)
        cur = bd.get(latest) if bd else None
        pv = bd.get(prev) if (bd and prev) else None
        delta = (cur - pv) if (cur is not None and pv is not None) else None
        out[b] = {"pct": cur, "delta": delta}

    fii_p, dii_p = out["fii"]["pct"], out["dii"]["pct"]
    fii_d, dii_d = out["fii"]["delta"], out["dii"]["delta"]
    inst_pct = None if (fii_p is None and dii_p is None) else (fii_p or 0) + (dii_p or 0)
    inst_delta = None if (fii_d is None and dii_d is None) else (fii_d or 0) + (dii_d or 0)
    out["institutional"] = {"pct": inst_pct, "delta": inst_delta}
    return out
