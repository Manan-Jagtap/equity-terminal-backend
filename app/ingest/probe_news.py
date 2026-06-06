"""
probe_news.py — map the IndianAPI news endpoint shapes so we can replace the
yfinance news fallback. Read-only. Saves probe_news_dump.json.

Run:
  railway run python3 -m app.ingest.probe_news
"""
import os, sys, json
import requests

BASE = os.getenv("INDIANAPI_BASE", "https://dev.indianapi.in").rstrip("/")
KEY = os.getenv("INDIANAPI_KEY", "").strip()
H = {"X-API-Key": KEY, "x-api-key": KEY}
OUT = os.path.join(os.path.dirname(__file__), "probe_news_dump.json")
dump = {}


def summarize(body):
    if isinstance(body, dict):
        return {k: ({"list_len": len(v), "first": v[0] if v else None} if isinstance(v, list)
                    else (list(v.keys()) if isinstance(v, dict) else v)) for k, v in body.items()}
    if isinstance(body, list):
        return {"list_len": len(body), "first": body[0] if body else None}
    return body


def get(label, path, params=None):
    try:
        r = requests.get(BASE + path, headers=H, params=params or {}, timeout=40)
        body = r.json(); code = r.status_code
    except Exception as e:
        body, code = f"{type(e).__name__}: {e}", "ERR"
    dump[label] = {"path": path, "params": params, "status": code, "body": body}
    print(f"[{code}] {label:<18} → {json.dumps(summarize(body), default=str)[:260]}")


def main():
    if not KEY or KEY.lower().startswith(("paste", "your")):
        sys.exit("Set INDIANAPI_KEY (or run via: railway run python -m app.ingest.probe_news)")
    print(f"BASE = {BASE}\n")
    get("company_news", "/company_news", {"stock_name": "TCS"})
    get("market_news", "/news", {"page_no": 1, "size": 10})
    get("ai_news", "/ai_news")
    with open(OUT, "w") as f:
        json.dump(dump, f, indent=2, default=str)
    print(f"\n✓ wrote {OUT} — upload it and I'll swap News to IndianAPI.")


if __name__ == "__main__":
    main()
