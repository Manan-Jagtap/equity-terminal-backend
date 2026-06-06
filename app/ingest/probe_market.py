"""
probe_market.py — map the Market Overview + batch-pricing endpoint shapes for
Phase 2 (Live Market Dashboard). Read-only. Saves probe_market_dump.json.

NB: live lists (trending / most-active) can be empty when the market is closed
(weekends/holidays); indices, 52-week and the batch POST still show structure.

Run:
  export INDIANAPI_KEY=your-real-key   (or use: railway run ...)
  python -m app.ingest.probe_market
"""
import os, sys, json
import requests

BASE = os.getenv("INDIANAPI_BASE", "https://dev.indianapi.in").rstrip("/")
KEY = os.getenv("INDIANAPI_KEY", "").strip()
H = {"X-API-Key": KEY, "x-api-key": KEY}
OUT = os.path.join(os.path.dirname(__file__), "probe_market_dump.json")
dump = {}


def summarize(body):
    """Compact structural summary of a JSON body."""
    if isinstance(body, dict):
        out = {}
        for k, v in body.items():
            if isinstance(v, list):
                out[k] = {"list_len": len(v), "first_item_keys": list(v[0].keys()) if v and isinstance(v[0], dict) else (v[0] if v else None)}
            elif isinstance(v, dict):
                out[k] = {"dict_keys": list(v.keys())}
            else:
                out[k] = v
        return out
    if isinstance(body, list):
        return {"list_len": len(body), "first_item_keys": list(body[0].keys()) if body and isinstance(body[0], dict) else (body[0] if body else None)}
    return body


def get(label, path, params=None):
    try:
        r = requests.get(BASE + path, headers=H, params=params or {}, timeout=40)
        body = r.json(); code = r.status_code
    except Exception as e:
        body, code = f"{type(e).__name__}: {e}", "ERR"
    dump[label] = {"path": path, "method": "GET", "params": params, "status": code, "body": body}
    print(f"[{code}] {label:<28} → {json.dumps(summarize(body))[:200]}")


def post(label, path, payload):
    try:
        r = requests.post(BASE + path, headers=H, json=payload, timeout=40)
        body = r.json(); code = r.status_code
    except Exception as e:
        body, code = f"{type(e).__name__}: {e}", "ERR"
    dump[label] = {"path": path, "method": "POST", "payload": payload, "status": code, "body": body}
    print(f"[{code}] {label:<28} → {json.dumps(summarize(body))[:200]}")


def main():
    if not KEY or KEY.lower().startswith(("paste", "your")):
        sys.exit("Set INDIANAPI_KEY (or run via: railway run python -m app.ingest.probe_market)")
    print(f"BASE = {BASE}\n")
    get("indices", "/indices")
    get("indices_NSE", "/indices", {"exchange": "NSE"})
    get("trending", "/trending")
    get("NSE_most_active", "/NSE_most_active")
    get("price_shockers", "/price_shockers")
    get("52w_high_low", "/fetch_52_week_high_low_data")
    get("commodities", "/commodities")
    post("nse_batch_price", "/nse_stock_batch_live_price",
         {"stock_symbols": ["TCS", "RELIANCE", "HDFCBANK"]})

    with open(OUT, "w") as f:
        json.dump(dump, f, indent=2, default=str)
    print(f"\n✓ wrote {OUT} — upload it and I'll build the dashboard.")


if __name__ == "__main__":
    main()
