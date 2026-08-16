"""
app/ipo_routes.py — IPO board from the licensed vendor feed.

  GET /api/ipo → { upcoming, active, closed, listed, as_of }

Each row: name, symbol, SME flag, price band, lot size, bidding/allotment/
listing dates, total subscription rate, listing price + gains, RHP link.
Cached 3h in-process (~8 vendor calls/day — negligible against the budget).

GMP (grey-market premium) is deliberately NOT published: it is unregulated
rumor-market data no licensed source carries; the verifiable analogues here
are the subscription rate and realised listing gains.
"""
import os
import time

import requests
from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["ipo"])

BASE = os.getenv("INDIANAPI_BASE", "https://stock.indianapi.in").rstrip("/")
ANALYST_BASE = os.getenv("INDIANAPI_ANALYST_BASE", "https://analyst.indianapi.in").rstrip("/")
KEY = os.getenv("INDIANAPI_KEY", "").strip()
_TTL = 3 * 3600
_cache: dict = {"ts": 0.0, "data": None}

_FIELDS = ("symbol", "name", "is_sme", "status", "min_price", "max_price",
           "issue_price", "lot_size", "min_bid_quantity",
           "bidding_start_date", "bidding_end_date", "allotment_date",
           "listing_date", "listing_price", "listing_gains",
           "total_subscription_rate", "additional_text", "document_url")


def _clean(rows, v2=None):
    v2 = v2 or {}
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        row = {k: r.get(k) for k in _FIELDS}
        extra = v2.get((r.get("symbol") or "").upper().strip()) or v2.get((r.get("name") or "").lower().strip())
        if extra:
            row["industry"] = extra.get("industry")
            row["isin"] = extra.get("isin")
            row["issue_type"] = extra.get("issueType")
            row["nse_enabled"] = extra.get("nseEnabled")
            row["bse_enabled"] = extra.get("bseEnabled")
            row["detail_id"] = extra.get("id")
        out.append(row)
    return out


def _record(data, *, empty_ok: bool = False) -> bool:
    """Report one vendor call's OUTCOME to the meter — spend was tick()ed at the
    call; this is the other question, "did upstream answer" (vendor_meter.record).
    Returns the judgement so a caller can drive its cache decision off the SAME
    predicate. Never raises: metering must not take the board down."""
    try:
        from app import vendor_meter as _vm
        ok = _vm.payload_ok(data, empty_ok=empty_ok)
        _vm.record(ok)
        return ok
    except Exception:
        return data is not None


@router.get("/ipo")
def ipo_board():
    now = time.time()
    if _cache["data"] is not None and now - _cache["ts"] < _TTL:
        return _cache["data"]
    data, v2, ok = None, {}, False      # ok: this call's outcome (see _record)
    if KEY:
        try:
            from app import vendor_meter; vendor_meter.tick()  # FIX-07
            resp = requests.get(BASE + "/ipo",
                                headers={"X-API-Key": KEY, "x-api-key": KEY},
                                timeout=25)
            if resp.status_code == 200:
                data = resp.json()
        except Exception:
            data = None
        # The board is never legitimately empty — the closed and listed lists
        # always carry names — so an empty or envelope 200 is upstream trouble,
        # and (below) must not overwrite the cached last-good board either: the
        # same purge-before-validate class #140 closed in market_routes.
        ok = _record(data)
        # /ipo/v2 (analyst host) adds industry, ISIN, NSE/BSE board flags and a
        # detail id — merged onto the base rows by symbol, then name.
        for st in ("upcoming", "open", "closed", "listed"):
            raw = None
            try:
                # Metered and outcome-tracked like the base call. These four
                # were invisible to both: quota burned uncounted, and a dead
                # analyst host never reached /api/health.
                from app import vendor_meter; vendor_meter.tick()
                r2 = requests.get(ANALYST_BASE + "/ipo/v2", headers={"x-api-key": KEY},
                                  params={"status": st}, timeout=20)
                if r2.status_code == 200:
                    raw = r2.json()
                    arr = raw if isinstance(raw, list) else (raw.get("data") or raw.get("ipos") or [])
                    for it in arr:
                        if isinstance(it, dict):
                            k = (it.get("symbol") or "").upper().strip() or (it.get("name") or "").lower().strip()
                            if k:
                                v2[k] = it
            except Exception:
                pass
            # A status bucket can be legitimately empty (no upcoming issues in a
            # dry month) — judged on the raw body so an envelope still fails.
            _record(raw, empty_ok=True)
    if not ok or not isinstance(data, dict):
        # serve last-known-good over an empty shell
        if _cache["data"] is not None:
            return _cache["data"]
        return {"upcoming": [], "active": [], "closed": [], "listed": [],
                "as_of": None, "available": False}
    payload = {
        "upcoming": _clean(data.get("upcoming"), v2),
        "active":   _clean(data.get("active"), v2),
        "closed":   _clean(data.get("closed"), v2),
        "listed":   _clean(data.get("listed"), v2),
        "as_of": time.strftime("%Y-%m-%d %H:%M"),
        "available": True,
    }
    _cache.update(ts=now, data=payload)
    return payload


ANALYST_HDR = {"x-api-key": KEY}


@router.get("/ipo/detail/{detail_id}")
def ipo_detail(detail_id: str):
    """Full detail for one IPO (analyst host /ipo/{id}): pricing, dates,
    reservation split, day-by-day subscription, registrar, prospectus."""
    d = None
    try:
        from app import vendor_meter; vendor_meter.tick()   # was unmetered, like /ipo/v2
        r = requests.get(f"{ANALYST_BASE}/ipo/{detail_id}", headers=ANALYST_HDR, timeout=20)
        if r.status_code == 200:
            d = r.json()
    except Exception:
        d = None
    # detail_id is caller-chosen on an unauthenticated route: an empty answer
    # for a junk id is upstream answering, not upstream down.
    _record(d, empty_ok=True)
    if d is not None:
        return d.get("data") if isinstance(d, dict) and "data" in d else d
    return {"available": False}
