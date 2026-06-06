"""
probe_quarterly.py — map IndianAPI's multi-quarter results shape so we can add
an Annual/Quarterly toggle to the Financials tab. Read-only.

Run:
  railway run python3 -m app.ingest.probe_quarterly
"""
import os, sys, json
import requests

BASE = os.getenv("INDIANAPI_BASE", "https://dev.indianapi.in").rstrip("/")
KEY = os.getenv("INDIANAPI_KEY", "").strip()
H = {"X-API-Key": KEY, "x-api-key": KEY}
OUT = os.path.join(os.path.dirname(__file__), "probe_quarterly_dump.json")
dump = {}


def get(label, path, params):
    try:
        r = requests.get(BASE + path, headers=H, params=params, timeout=40)
        body = r.json(); code = r.status_code
    except Exception as e:
        body, code = f"{type(e).__name__}: {e}", "ERR"
    dump[label] = {"path": path, "params": params, "status": code, "body": body}
    # compact structural print
    if isinstance(body, dict):
        shape = {k: (f"dict[{len(v)}]" if isinstance(v, dict) else f"list[{len(v)}]" if isinstance(v, list) else type(v).__name__)
                 for k, v in body.items()}
    elif isinstance(body, list):
        shape = f"list[{len(body)}], first_keys={list(body[0].keys()) if body and isinstance(body[0], dict) else body[:1]}"
    else:
        shape = body
    print(f"[{code}] {label}: {json.dumps(shape, default=str)[:300]}")


def main():
    if not KEY or KEY.lower().startswith(("paste", "your")):
        sys.exit("Set INDIANAPI_KEY (or run via: railway run ...)")
    print(f"BASE = {BASE}\n")
    # Multi-quarter time series (Screener-style quarterly results)
    get("histstats_quarter", "/historical_stats", {"stock_name": "TCS", "stats": "quarter_results"})
    # Latest single quarter (for comparison)
    get("statement_quarter", "/statement", {"stock_name": "TCS", "stats": "quarter_results"})
    with open(OUT, "w") as f:
        json.dump(dump, f, indent=2, default=str)
    print(f"\n✓ wrote {OUT} — upload it and I'll build the quarterly toggle.")


if __name__ == "__main__":
    main()
