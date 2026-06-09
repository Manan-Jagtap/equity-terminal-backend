"""
app/ingest/probe_new.py — one-off shape probe for the IndianAPI endpoints we
don't yet leverage. Run server-side (it needs INDIANAPI_KEY) via the scheduler
flag RUN_PROBE_ENDPOINTS=1; it prints a COMPACT shape of each response to the
logs so the parsers can be built accurately. Read-only; writes nothing.

Endpoints probed: /mf_holdings, /corporate_actions, /documents,
/1D_intraday_data (POST), /nse_stock_batch_live_price (POST), /ai_news.
"""
import os, json, requests

BASE = os.getenv("INDIANAPI_BASE", "https://dev.indianapi.in").rstrip("/")
KEY = os.getenv("INDIANAPI_KEY", "").strip()
H = {"X-API-Key": KEY, "x-api-key": KEY}


def _shape(v, depth=0, maxdepth=3):
    """A compact structural sketch: types + dict keys + first list element."""
    pad = "  " * depth
    if isinstance(v, dict):
        if depth >= maxdepth:
            return "{…%d keys}" % len(v)
        return "{\n" + "\n".join(
            f"{pad}  {k}: {_shape(val, depth+1, maxdepth)}" for k, val in list(v.items())[:25]
        ) + f"\n{pad}}}"
    if isinstance(v, list):
        if not v:
            return "[]"
        return f"[{len(v)}×] " + _shape(v[0], depth + 1, maxdepth)
    if isinstance(v, str):
        return f"str({v[:40]!r})"
    return type(v).__name__ + (f"({v})" if isinstance(v, (int, float, bool)) else "")


def _probe(method, path, params=None, body=None):
    print(f"\n===== {method} {path}  params={params} body={body} =====")
    try:
        if method == "POST":
            r = requests.post(BASE + path, headers=H, json=body, params=params, timeout=30)
        else:
            r = requests.get(BASE + path, headers=H, params=params, timeout=30)
        ct = r.headers.get("content-type", "")
        print(f"  status={r.status_code}  content-type={ct}")
        if "image" in ct:
            print(f"  IMAGE bytes={len(r.content)}")
            return
        try:
            print("  shape: " + _shape(r.json()))
        except Exception:
            print("  text: " + r.text[:300])
    except Exception as e:
        print(f"  ERROR {e}")


def run():
    if not KEY:
        print("INDIANAPI_KEY not set — cannot probe.")
        return
    T = "RELIANCE"
    TID = "S0003018"   # Reliance ticker_id (for logo / id-keyed endpoints)

    # Ownership deep-dive
    _probe("GET", "/mf_holdings", {"stock_name": T})
    _probe("GET", "/mf_holdings", {"stock_id": T})
    # Corporate actions & filings
    _probe("GET", "/corporate_actions", {"stock_name": T})
    _probe("GET", "/documents", {"stock_name": T})
    _probe("GET", "/documents", {"stock_name": T, "year": 2025})
    # Intraday + batch price
    _probe("POST", "/1D_intraday_data", body={"stock_name": T})
    _probe("GET", "/1D_intraday_data", {"stock_name": T})
    _probe("POST", "/nse_stock_batch_live_price", body={"stocks": ["RELIANCE", "TCS", "INFY"]})
    _probe("POST", "/nse_stock_batch_live_price", body=["RELIANCE", "TCS"])
    # AI news — try a few plausible categories
    for cat in ("market", "business", "economy", "ipo", "all", "stocks"):
        _probe("GET", "/ai_news", {"category": cat})
    # Logo (confirm it serves an image at /logo/{ticker_id})
    _probe("GET", f"/logo/{TID}")
    _probe("GET", f"/logo/{TID}.png")
    print("\n[probe-new] done.")


if __name__ == "__main__":
    run()
