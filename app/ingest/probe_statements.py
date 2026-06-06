"""
probe_statements.py — map IndianAPI's Balance Sheet, Cash Flow and Ratios
shapes (original line items, per sector) so they can be rendered as-is. Probes
a manufacturer (TCS) and an NBFC (SHRIRAMFIN) to compare. Read-only.

Run:
  railway run python3 -m app.ingest.probe_statements
"""
import os, sys, json
import requests

BASE = os.getenv("INDIANAPI_BASE", "https://dev.indianapi.in").rstrip("/")
KEY = os.getenv("INDIANAPI_KEY", "").strip()
H = {"X-API-Key": KEY, "x-api-key": KEY}
OUT = os.path.join(os.path.dirname(__file__), "probe_statements_dump.json")
dump = {}


def get(label, path, params):
    try:
        r = requests.get(BASE + path, headers=H, params=params, timeout=40)
        body = r.json(); code = r.status_code
    except Exception as e:
        body, code = f"{type(e).__name__}: {e}", "ERR"
    dump[label] = {"path": path, "params": params, "status": code, "body": body}
    if isinstance(body, dict):
        line_items = list(body.keys())
        first = body.get(line_items[0]) if line_items else None
        periods = list(first.keys()) if isinstance(first, dict) else first
        print(f"[{code}] {label}\n   line_items={line_items}\n   periods={periods}\n")
    else:
        print(f"[{code}] {label}: {str(body)[:160]}\n")


def main():
    if not KEY or KEY.lower().startswith(("paste", "your")):
        sys.exit("Set INDIANAPI_KEY (or run via: railway run ...)")
    print(f"BASE = {BASE}\n")
    for tk in ("TCS", "SHRIRAMFIN"):
        for stat in ("balancesheet", "cashflow", "ratios", "profit_loss_stats"):
            get(f"{tk}:{stat}", "/historical_stats", {"stock_name": tk, "stats": stat})
    with open(OUT, "w") as f:
        json.dump(dump, f, indent=2, default=str)
    print(f"✓ wrote {OUT} — upload it and I'll render BS / CF / Ratios as-is.")


if __name__ == "__main__":
    main()
