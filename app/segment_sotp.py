"""
app/segment_sotp.py — data-driven Sum-of-the-Parts from REPORTED segment financials.

Indian companies report Segment Information (Ind-AS 108) in every quarterly /
annual filing: segment revenue + segment result (≈ segment EBIT). IndianAPI
doesn't carry it, so the numbers are entered ONCE per conglomerate (a ~5-row
read off the public filing — no AI, no API cost) and this module values each
segment TRANSPARENTLY:

  · an operating segment at `segment EBIT × the SECTOR EV/EBITDA multiple`
  · a listed-subsidiary segment at the stake's market value

summed, less net debt, per share. That turns the conglomerate SOTPs from
hand-picked round numbers into a self-justifying breakdown you can audit segment
by segment. Stored in KVStore under SEG_KEY as
{TICKER: {as_of, source, net_debt, shares, segments:[…]}}; when present it
OVERRIDES the illustrative preset in alt_models.alternative_intrinsic.

Each segment is one of:
  {"name": str, "kind": "operating", "ebit": <₹cr>, "sector": <SECTOR key>}
  {"name": str, "kind": "stake",     "value": <₹cr stake market value>}
  {"name": str, "value": <₹cr>}      # explicit fallback
"""
from __future__ import annotations

SEG_KEY = "segment_financials_v1"


def _multiple_for(sector_key: str) -> float:
    """Sector EV/EBITDA for an operating segment. Falls back to a mid multiple."""
    from app import sector_params as SP
    p = SP.SECTOR_PARAMS.get((sector_key or "").upper())
    m = (p or {}).get("exit_ev_ebitda")
    return float(m) if m else 12.0


def _segment_value(seg: dict) -> tuple[float | None, str]:
    """(value in ₹cr, how it was derived) for one segment. None if not valuable."""
    if not isinstance(seg, dict):
        return None, ""
    kind = (seg.get("kind") or "").lower()
    if seg.get("value") is not None and (kind == "stake" or seg.get("ebit") is None):
        try:
            return float(seg["value"]), "stake / stated market value"
        except (TypeError, ValueError):
            return None, ""
    ebit = seg.get("ebit")
    if ebit is not None:
        try:
            mult = _multiple_for(seg.get("sector"))
            return float(ebit) * mult, f"EBIT ₹{float(ebit):.0f}cr × {mult:.0f}x ({seg.get('sector') or 'sector'})"
        except (TypeError, ValueError):
            return None, ""
    return None, ""


def compute_sotp(segments: list[dict], net_debt: float, shares: float) -> dict | None:
    """Per-share SOTP from valued segments: Σ segment value − net debt, ÷ shares."""
    if not segments or not shares or shares <= 0:
        return None
    comps, total = [], 0.0
    for s in segments:
        v, how = _segment_value(s)
        if v is None or v <= 0:
            continue
        total += v
        comps.append({"label": s.get("name") or "Segment", "value": round(v, 1), "basis": how})
    if total <= 0 or not comps:
        return None
    equity = total - (net_debt or 0.0)
    if equity <= 0:
        return None
    return {
        "intrinsic": equity / shares,
        "method": "Sum-of-the-Parts",
        "components": comps,
        "note": ("Sum-of-the-parts on REPORTED segment financials — operating segments "
                 "at segment EBIT × the sector EV/EBITDA multiple, listed stakes at market "
                 "value; less net debt, per share. Data-driven, not hand-set."),
    }


def normalise_segments(raw: list) -> list[dict]:
    """Clean a list of user-entered segment rows into the stored shape. Drops rows
    with neither an ebit nor a value."""
    out = []
    for s in raw if isinstance(raw, list) else []:
        if not isinstance(s, dict) or not s.get("name"):
            continue
        if s.get("ebit") is None and s.get("value") is None:
            continue
        out.append({
            "name": str(s["name"])[:60],
            "kind": (s.get("kind") or ("stake" if s.get("value") is not None and s.get("ebit") is None else "operating")),
            "ebit": s.get("ebit"),
            "value": s.get("value"),
            "sector": (s.get("sector") or "MANUFACTURING"),
            "revenue": s.get("revenue"),
        })
    return out


def get_segment_sotp(db, ticker: str, net_debt: float, shares: float) -> dict | None:
    """Stored segment-SOTP for a ticker, computed live. None if none entered yet."""
    from app.manager_engine import _kv_get
    store = _kv_get(db, SEG_KEY) or {}
    rec = store.get((ticker or "").upper())
    if not rec:
        return None
    nd = rec.get("net_debt") if rec.get("net_debt") is not None else net_debt
    sh = rec.get("shares") or shares
    out = compute_sotp(rec.get("segments") or [], nd, sh)
    if out and rec.get("as_of"):
        out["note"] += f" Segments as of {rec['as_of']}."
    return out


def store_segments(db, ticker: str, segments: list[dict], *, as_of: str | None = None,
                   net_debt: float | None = None, shares: float | None = None,
                   source: str | None = None) -> dict:
    """Merge one company's segments into the KVStore."""
    from app.manager_engine import _kv_put, _kv_get
    store = dict(_kv_get(db, SEG_KEY) or {})
    store[(ticker or "").upper()] = {
        "segments": normalise_segments(segments), "as_of": as_of, "source": source,
        "net_debt": net_debt, "shares": shares,
    }
    _kv_put(db, SEG_KEY, store)
    return store[(ticker or "").upper()]


def load_store(db) -> dict:
    """The whole verified-segment store: {TICKER: {segments, as_of, source, …}}."""
    from app.manager_engine import _kv_get
    return dict(_kv_get(db, SEG_KEY) or {})


def delete_segments(db, ticker: str) -> bool:
    """Remove one company's verified segments — it falls back to the illustrative
    preset (or the plain engine blend). True if an entry was removed."""
    from app.manager_engine import _kv_put, _kv_get
    store = dict(_kv_get(db, SEG_KEY) or {})
    tk = (ticker or "").upper()
    if tk not in store:
        return False
    del store[tk]
    _kv_put(db, SEG_KEY, store)
    return True
