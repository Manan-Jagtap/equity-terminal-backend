"""
probe_profile.py — map the company-profile / document endpoint shapes for the
generalized Business tab: concalls, annual reports, credit ratings, recent
announcements. Read-only. Saves probe_profile_dump.json.

Run:
  railway run python3 -m app.ingest.probe_profile
"""
import os, sys, json
import requests

BASE = os.getenv("INDIANAPI_BASE", "https://dev.indianapi.in").rstrip("/")
KEY = os.getenv("INDIANAPI_KEY", "").strip()
H = {"X-API-Key": KEY, "x-api-key": KEY}
OUT = os.path.join(os.path.dirname(__file__), "probe_profile_dump.json")
dump = {}


def shape(b):
    if isinstance(b, dict):
        return {k: (f"list[{len(v)}] first={v[0] if v else None}" if isinstance(v, list)
                    else (list(v.keys()) if isinstance(v, dict) else v)) for k, v in b.items()}
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
    print(f"[{code}] {label}: {json.dumps(shape(body), default=str)[:320]}\n")


def main():
    if not KEY or KEY.lower().startswith(("paste", "your")):
        sys.exit("Set INDIANAPI_KEY (or run via: railway run ...)")
    print(f"BASE = {BASE}\n")
    for label, path in (("concalls", "/concalls"), ("annual_reports", "/annual_reports"),
                        ("credit_ratings", "/credit_ratings"), ("recent_announcements", "/recent_announcements")):
        get(label, path, {"stock_name": "TCS"})
    with open(OUT, "w") as f:
        json.dump(dump, f, indent=2, default=str)
    print(f"✓ wrote {OUT} — upload it and I'll build the Business tab for all 50.")


if __name__ == "__main__":
    main()
