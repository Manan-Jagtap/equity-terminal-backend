"""
probe_dev.py — one-shot discovery of the IndianAPI Developer (v2) endpoints
                AND a resolution check for the tickers that came in wrong.

Does NOT touch the database. Two jobs:
  1. IDENTITY CHECK (all tickers incl. the 4 bad ones): call /stock with
     name= and symbol= and dump what each resolves to (company name, symbol,
     price, #annual-years) so we can fix BAJAJ-AUTO / LT / M&M / SBILIFE with
     an exact, collision-proof resolver.
  2. SHAPE DISCOVERY (TCS + RELIANCE): hit statement / historical_stats /
     target_price / forecasts so the new tabs can be wired first-try.

Writes app/ingest/probe_dump.json.

Run:
  export INDIANAPI_KEY=your-real-key
  python -m app.ingest.probe_dev
"""
import os, sys, json, time
import requests

DEV_BASE = os.getenv("INDIANAPI_BASE", "https://dev.indianapi.in")
ALT_BASE = "https://stock.indianapi.in"
KEY = os.getenv("INDIANAPI_KEY", "").strip()

# Identity check for everything that looked wrong (+ two good controls).
IDENTITY_TICKERS = ["BAJAJ-AUTO", "LT", "M&M", "SBILIFE", "BAJFINANCE", "TCS"]
# Full new-endpoint shape discovery on two clean names.
FULL_TICKERS = ["TCS", "RELIANCE"]

OUT = os.path.join(os.path.dirname(__file__), "probe_dump.json")
dump = {}


def call(base, path, params, label, quiet=False):
    url = base + path
    try:
        r = requests.get(url, headers={"x-api-key": KEY, "X-API-Key": KEY},
                         params=params, timeout=60)
        ok = r.status_code == 200
        body = r.json() if ok else r.text[:300]
    except Exception as e:
        ok, body, r = False, f"{type(e).__name__}: {e}", None
    code = getattr(r, "status_code", "ERR")
    if not quiet:
        print(f"  [{code}] {label:<40} {params}")
    dump[label] = {"url": url, "params": params, "status": code, "ok": ok, "body": body}
    return body if ok else None


def identity(stock):
    """Extract the few fields that tell us *which company* this actually is."""
    if not isinstance(stock, dict):
        return {}
    keys = ("companyName", "company_name", "name", "shortName", "symbol",
            "nseScriptCode", "bseScriptCode", "tickerId", "id", "industry")
    out = {k: stock.get(k) for k in keys if stock.get(k) is not None}
    cp = stock.get("currentPrice") or {}
    if isinstance(cp, dict):
        out["price_NSE"] = cp.get("NSE")
        out["price_BSE"] = cp.get("BSE")
    fin = stock.get("financials") or stock.get("stockFinancialData") or []
    out["annual_years"] = sum(1 for e in fin if isinstance(e, dict) and e.get("Type") == "Annual")
    out["all_top_keys"] = list(stock.keys())
    return out


def find_ids(stock):
    cands = {}
    if not isinstance(stock, dict):
        return cands
    for k, v in stock.items():
        if ("id" in k.lower() or "tid" in k.lower()) and isinstance(v, (str, int)) and str(v).strip():
            cands[k] = v
    for parent in ("companyProfile", "company", "meta", "info"):
        sub = stock.get(parent)
        if isinstance(sub, dict):
            for k, v in sub.items():
                if "id" in k.lower() and isinstance(v, (str, int)):
                    cands[f"{parent}.{k}"] = v
    return cands


def identity_check(ticker, base):
    print(f"\n--- identity: {ticker} ---")
    by_name = call(base, "/stock", {"name": ticker}, f"{ticker}:by_name")
    by_sym = call(base, "/stock", {"symbol": ticker}, f"{ticker}:by_symbol")
    idn = {"by_name": identity(by_name), "by_symbol": identity(by_sym)}
    dump[f"{ticker}:IDENTITY"] = idn
    for k in ("by_name", "by_symbol"):
        d = idn[k]
        if d:
            print(f"    {k:<10} → {d.get('companyName') or d.get('name')} | "
                  f"sym={d.get('symbol') or d.get('nseScriptCode')} | "
                  f"NSE=₹{d.get('price_NSE')} | {d.get('annual_years')} yrs")
        else:
            print(f"    {k:<10} → (no result)")


def full_probe(ticker, base):
    print(f"\n=== full probe: {ticker} ===")
    stock = call(base, "/stock", {"name": ticker}, f"{ticker}:stock")
    if isinstance(stock, dict):
        dump[f"{ticker}:id_candidates"] = find_ids(stock)
        print(f"      id candidates: {find_ids(stock)}")
    for stat in ("quarter_results", "ttm_results", "yoy_results", "balancesheet", "cashflow"):
        call(base, "/statement", {"stock_name": ticker, "stats": stat}, f"{ticker}:statement:{stat}")
        time.sleep(0.5)
    for stat in ("ratios", "profit_loss_stats"):
        call(base, "/historical_stats", {"stock_name": ticker, "stats": stat}, f"{ticker}:histstats:{stat}")
        time.sleep(0.5)
    ids = dump.get(f"{ticker}:id_candidates", {})
    for label, sid in list(ids.items()) + [("ticker", ticker)]:
        sid = str(sid)
        tp = call(base, "/stock_target_price", {"stock_id": sid}, f"{ticker}:target[{label}={sid}]")
        if tp:
            call(base, "/stock_forecasts",
                 {"stock_id": sid, "measure_code": "EPS", "period_type": "Annual",
                  "data_type": "Estimates", "age": "Current"}, f"{ticker}:fc_EPS_est[{sid}]")
            call(base, "/stock_forecasts",
                 {"stock_id": sid, "measure_code": "SAL", "period_type": "Annual",
                  "data_type": "Estimates", "age": "Current"}, f"{ticker}:fc_SAL_est[{sid}]")
            dump[f"{ticker}:WORKING_STOCK_ID"] = sid
            break
        time.sleep(0.5)


def main():
    if not KEY or KEY.lower().startswith(("paste", "your")):
        sys.exit("Set INDIANAPI_KEY to your real key first:  export INDIANAPI_KEY=...")

    print("Checking which base URL the key works on…")
    base = DEV_BASE
    if call(DEV_BASE, "/stock", {"name": "TCS"}, "_basecheck:dev", quiet=True) is None:
        print("  dev. base failed — falling back to stock. base")
        base = ALT_BASE
        call(ALT_BASE, "/stock", {"name": "TCS"}, "_basecheck:stock", quiet=True)
    dump["_WORKING_BASE"] = base
    print(f"  → using {base}")

    call(base, "/usage", {}, "_usage")

    print("\n########## IDENTITY CHECKS ##########")
    for t in IDENTITY_TICKERS:
        identity_check(t, base)
        time.sleep(0.6)

    print("\n########## SHAPE DISCOVERY ##########")
    for t in FULL_TICKERS:
        full_probe(t, base)
        time.sleep(0.8)

    with open(OUT, "w") as f:
        json.dump(dump, f, indent=2, default=str)
    print(f"\n✓ Wrote {OUT}")
    print("  Working base:", dump.get("_WORKING_BASE"))
    print("  Send me probe_dump.json and I'll fix the 4 tickers + wire the new tabs.")


if __name__ == "__main__":
    main()
