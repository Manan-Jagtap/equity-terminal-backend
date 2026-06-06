"""
probe_forecast.py — dump the FULL EPS forecast for TCS so we can wire forward
P/E precisely. Prints a compact per-period summary (actual vs estimate + the
Estimates field names) and saves the raw JSON.

Run:
  export INDIANAPI_KEY=your-real-key
  python -m app.ingest.probe_forecast
"""
import os, sys, json
import requests

BASE = os.getenv("INDIANAPI_BASE", "https://dev.indianapi.in").rstrip("/")
KEY = os.getenv("INDIANAPI_KEY", "").strip()
OUT = os.path.join(os.path.dirname(__file__), "probe_forecast_dump.json")


def main():
    if not KEY or KEY.lower().startswith(("paste", "your")):
        sys.exit("Set INDIANAPI_KEY to your real key first.")

    params = {"stock_id": "TCS", "measure_code": "EPS",
              "period_type": "Annual", "data_type": "Estimates", "age": "Current"}
    r = requests.get(BASE + "/stock_forecasts", params=params,
                     headers={"X-API-Key": KEY, "x-api-key": KEY}, timeout=40)
    print(f"[{r.status_code}] /stock_forecasts {params}\n")
    data = r.json()
    with open(OUT, "w") as f:
        json.dump(data, f, indent=2, default=str)

    periods = (data or {}).get("periods") or []
    print(f"top-level keys: {list(data.keys()) if isinstance(data, dict) else type(data)}")
    print(f"{len(periods)} periods:\n")
    for p in periods:
        rel = (p.get("RelativePeriod") or {}).get("Number")
        yr = (p.get("FiscalPeriod") or {}).get("Year")
        act = p.get("Actuals")
        est = p.get("Estimates")
        line = f"  N={rel:>3}  FY{yr}  "
        if act:
            inner = (act.get("Actual") or [{}])[0]
            line += f"ACTUAL Reported={inner.get('Reported')}"
        if est:
            inner = (est.get("Estimate") or est.get("Estimates") or [{}])
            inner = inner[0] if isinstance(inner, list) else inner
            keys = list(inner.keys()) if isinstance(inner, dict) else "?"
            mean = inner.get("Mean") if isinstance(inner, dict) else None
            line += f"ESTIMATE Mean={mean}  keys={keys}"
        if not act and not est:
            line += f"keys={list(p.keys())}"
        print(line)

    print(f"\n✓ wrote {OUT} — paste the summary above (or upload the file).")


if __name__ == "__main__":
    main()
