"""
app/segment_sotp.py — data-driven Sum-of-the-Parts from REPORTED segment financials.

Indian companies report Segment Information (Ind-AS 108) in every quarterly /
annual filing: segment revenue + segment result (≈ segment EBIT). IndianAPI
doesn't carry it, so this module:

  1. EXTRACTS the segment table from filing text with an LLM (Claude Haiku, the
     same raw-HTTP path news_routes uses — opt-in, ANTHROPIC_API_KEY).
  2. VALUES each segment transparently — an operating segment at
     `segment EBIT × the SECTOR EV/EBITDA multiple`, a listed-subsidiary segment
     at the stake's market value — summed, less net debt, per share.

That turns the conglomerate SOTPs from hand-picked round numbers into a
self-justifying breakdown you can audit segment by segment. Stored in KVStore
under SEG_KEY as {TICKER: {as_of, source, net_debt, shares, segments:[…]}}; when
present it OVERRIDES the illustrative preset in alt_models.alternative_intrinsic.

A segment is one of:
  {"name": str, "kind": "operating", "ebit": <₹cr>, "sector": <SECTOR key>}
  {"name": str, "kind": "stake",     "value": <₹cr stake market value>}
  {"name": str, "value": <₹cr>}      # explicit fallback
"""
from __future__ import annotations
import os
import json as _json

SEG_KEY = "segment_financials_v1"
_HAIKU = "claude-haiku-4-5-20251001"
_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


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


def get_segment_sotp(db, ticker: str, net_debt: float, shares: float) -> dict | None:
    """Stored segment-SOTP for a ticker, computed live. None if not extracted yet."""
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
    """Merge one company's extracted segments into the KVStore."""
    from app.manager_engine import _kv_put, _kv_get
    store = dict(_kv_get(db, SEG_KEY) or {})
    store[(ticker or "").upper()] = {
        "segments": segments, "as_of": as_of, "source": source,
        "net_debt": net_debt, "shares": shares,
    }
    _kv_put(db, SEG_KEY, store)
    return store[(ticker or "").upper()]


# ── LLM extraction ───────────────────────────────────────────────────────────
_PROMPT = """You are a financial-data extractor. From the filing text below for {name},
extract the SEGMENT INFORMATION (Ind-AS 108) for the MOST RECENT period reported.

Return ONLY a JSON array, one object per business segment, with keys:
  "name"    : segment name (string)
  "revenue" : segment revenue in Rs crore (number, null if absent)
  "ebit"    : segment result / segment profit before interest & tax, in Rs crore (number, null if absent)
  "kind"    : "operating" for a business segment; "stake" if the segment is really a listed-subsidiary holding
  "sector"  : ONE of BANK NBFC INSURANCE IT_SERVICES CONSUMER CONSUMER_DISC PHARMA AUTO METAL CEMENT
              ENERGY UTILITIES TELECOM MANUFACTURING CHEMICALS REALTY AVIATION CABLES CAPITAL_GOODS
              CONSTRUCTION DEFENCE TEXTILES LOGISTICS PAPER SUGAR — your best single classification.
Ignore "unallocated" / elimination rows. Numbers in Rs crore (convert if in millions/lakhs).
No prose, no markdown — just the JSON array.

FILING TEXT:
{text}
"""


def extract_segments(company_name: str, text: str, api_key: str | None = None) -> list[dict]:
    """Extract the reported segment table from filing text via Claude. Returns
    [] on any failure (never raises)."""
    import requests
    api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key or not text:
        return []
    body = {
        "model": _HAIKU, "max_tokens": 1500,
        "messages": [{"role": "user",
                      "content": _PROMPT.format(name=company_name, text=text[:60000])}],
    }
    try:
        r = requests.post(_ANTHROPIC_URL, json=body, timeout=60,
                          headers={"content-type": "application/json", "x-api-key": api_key,
                                   "anthropic-version": "2023-06-01"})
        r.raise_for_status()
        txt = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text").strip()
        if txt.startswith("```"):
            txt = txt.strip("`").split("\n", 1)[-1] if "\n" in txt else txt.strip("`")
        i, j = txt.find("["), txt.rfind("]")
        arr = _json.loads(txt[i:j + 1]) if i >= 0 and j > i else []
        out = []
        for s in arr if isinstance(arr, list) else []:
            if isinstance(s, dict) and s.get("name"):
                out.append({"name": str(s["name"])[:60],
                            "revenue": s.get("revenue"), "ebit": s.get("ebit"),
                            "kind": (s.get("kind") or "operating"),
                            "sector": (s.get("sector") or "MANUFACTURING")})
        return out
    except Exception:
        return []
