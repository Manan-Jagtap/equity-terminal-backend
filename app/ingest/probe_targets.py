"""
probe_targets.py — figure out why /stock_target_price and /stock_forecasts
return 'An error occurred'. Tries several id forms + parameter combos for TCS
and prints the RAW status + body of each so we can fix it in one go.

Run:
  export INDIANAPI_KEY=your-real-key
  python -m app.ingest.probe_targets
"""
import os, sys, json
import requests

BASE = os.getenv("INDIANAPI_BASE", "https://dev.indianapi.in").rstrip("/")
KEY = os.getenv("INDIANAPI_KEY", "").strip()
H = {"X-API-Key": KEY, "x-api-key": KEY}

# TCS identifiers
TICKER_ID = "S0003051"
ISIN = "INE467B01029"
OUT = os.path.join(os.path.dirname(__file__), "probe_targets_dump.json")
dump = {}


def show(label, path, params):
    try:
        r = requests.get(BASE + path, headers=H, params=params, timeout=40)
        body = r.json()
        code = r.status_code
    except Exception as e:
        body, code = f"{type(e).__name__}: {e}", "ERR"
    short = json.dumps(body)[:240] if not isinstance(body, str) else body[:240]
    print(f"[{code}] {label}\n        params={params}\n        → {short}\n")
    dump[label] = {"path": path, "params": params, "status": code, "body": body}


def main():
    if not KEY or KEY.lower().startswith(("paste", "your")):
        sys.exit("Set INDIANAPI_KEY to your real key first.")

    print(f"BASE = {BASE}\n")

    print("===== /stock_target_price — id-value variants (param name = stock_id) =====")
    for label, val in [("tickerId S0003051", TICKER_ID), ("numeric 3051", "3051"),
                        ("numeric 0003051", "0003051"), ("ISIN", ISIN), ("name TCS", "TCS")]:
        show(f"target stock_id={label}", "/stock_target_price", {"stock_id": val})

    print("===== /stock_target_price — alternate param NAMES (value = tickerId) =====")
    for pname in ("stockId", "id", "stock_name", "name", "symbol"):
        show(f"target {pname}=tickerId", "/stock_target_price", {pname: TICKER_ID})

    print("===== /stock_forecasts — variants =====")
    base_fc = {"stock_id": TICKER_ID, "measure_code": "EPS",
               "period_type": "Annual", "data_type": "Estimates", "age": "Current"}
    show("forecast EPS/Annual/Estimates/Current (tickerId)", "/stock_forecasts", base_fc)
    show("forecast EPS/Annual/Actuals/Current", "/stock_forecasts", {**base_fc, "data_type": "Actuals"})
    show("forecast EPS/Annual/Estimates/OneWeekAgo", "/stock_forecasts", {**base_fc, "age": "OneWeekAgo"})
    show("forecast EPS/Interim/Estimates/Current", "/stock_forecasts", {**base_fc, "period_type": "Interim"})
    show("forecast stock_id=ISIN", "/stock_forecasts", {**base_fc, "stock_id": ISIN})
    show("forecast stock_id=numeric", "/stock_forecasts", {**base_fc, "stock_id": "3051"})

    with open(OUT, "w") as f:
        json.dump(dump, f, indent=2, default=str)
    print(f"✓ wrote {OUT} — send it to me if anything returned 200.")


if __name__ == "__main__":
    main()
