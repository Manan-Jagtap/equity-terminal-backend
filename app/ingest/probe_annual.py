"""
probe_annual.py — find the annual P&L in the SAME Screener format as the
quarterly results, so the Annual income statement can match the Quarterly one.
Tries /historical_stats with stats=yoy_results (and a couple of fallbacks).
Read-only. Writes probe_annual_dump.json.

Run:
  railway run python3 -m app.ingest.probe_annual
"""
import os, sys, json
import requests

BASE = os.getenv("INDIANAPI_BASE", "https://dev.indianapi.in").rstrip("/")
KEY = os.getenv("INDIANAPI_KEY", "").strip()
H = {"X-API-Key": KEY, "x-api-key": KEY}
OUT = os.path.join(os.path.dirname(__file__), "probe_annual_dump.json")
dump = {}


def shape(b):
    if isinstance(b, dict):
        return {k: (f"dict[{len(v)}] keys={list(v.keys())[:6]}" if isinstance(v, dict)
                    else f"list[{len(v)}]" if isinstance(v, list) else v) for k, v in list(b.items())[:14]}
    if isinstance(b, list):
        return {"list_len": len(b), "first": b[0] if b else None}
    return b


def get(label, path, params):
    try:
        r = requests.get(BASE + path, headers=H, params=params, timeout=40)
        body = r.json(); code = r.status_code
    except Exception as e:
        body, code = f"{type(e).__name__}: {e}", "ERR"
    dump[label] = {"path": path, "params": params, "status": code, "body": body}
    print(f"[{code}] {label}: {json.dumps(shape(body), default=str)[:340]}\n")


def main():
    if not KEY or KEY.lower().startswith(("paste", "your")):
        sys.exit("Set INDIANAPI_KEY (or run via: railway run ...)")
    print(f"BASE = {BASE}\n")
    get("histstats_yoy", "/historical_stats", {"stock_name": "TCS", "stats": "yoy_results"})
    get("statement_yoy", "/statement", {"stock_name": "TCS", "stats": "yoy_results"})
    with open(OUT, "w") as f:
        json.dump(dump, f, indent=2, default=str)
    print(f"✓ wrote {OUT} — upload it and I'll match Annual to the Quarterly format.")


if __name__ == "__main__":
    main()
