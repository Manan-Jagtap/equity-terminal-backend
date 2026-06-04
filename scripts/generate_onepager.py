"""
scripts/generate_onepager.py — generate NBFC one-pager PDFs end to end.

Extracts each ticker's quarter data (IP+transcript from R2 -> Claude), caches
the extraction JSON, assembles a peer-comparison block across the set, and
renders an A4 PDF per company.

Examples:
    # Full pilot: extract all three, cross-compare, render all
    railway run python -m scripts.generate_onepager MUTHOOTFIN IIFL FEDFINA --quarter Q4FY26

    # Re-render from cached extractions WITHOUT calling the API again (free):
    railway run python -m scripts.generate_onepager MUTHOOTFIN IIFL FEDFINA --quarter Q4FY26 --from-cache

Output: ./onepager_out/<TICKER>_<QUARTER>.pdf  and  <TICKER>_<QUARTER>.json
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time

from app.onepager.extract import extract_company_quarter
from app.onepager.peers import build_peer_block
from app.onepager.render import render_pdf

OUT_DIR = os.environ.get("ONEPAGER_OUT", "onepager_out")


def _cache_path(ticker, quarter):
    return os.path.join(OUT_DIR, f"{ticker.upper()}_{quarter.upper()}.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tickers", nargs="+")
    ap.add_argument("--quarter", default="Q4FY26")
    ap.add_argument("--from-cache", action="store_true",
                    help="render from cached extraction JSON, skip the API")
    ap.add_argument("--no-peers", action="store_true")
    ap.add_argument("--api-sleep", type=float, default=20.0,
                    help="seconds to wait between company extractions (rate-limit cushion)")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    quarter = args.quarter.upper()
    extractions = {}

    for ticker in args.tickers:
        tk = ticker.upper()
        cp = _cache_path(tk, quarter)
        if args.from_cache:
            if not os.path.exists(cp):
                print(f"✗ {tk}: no cache at {cp}; run without --from-cache first")
                continue
            with open(cp) as f:
                extractions[tk] = json.load(f)
            print(f"• {tk}: loaded cached extraction")
        else:
            print(f"• {tk} {quarter}: extracting (calling Claude)…", flush=True)
            try:
                data = extract_company_quarter(tk, quarter)
            except Exception as e:
                print(f"✗ {tk}: extraction failed: {e}")
                continue
            with open(cp, "w") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            extractions[tk] = data
            kpis = len(data.get("headline_kpis", []))
            comm = len(data.get("commentary", []))
            print(f"  ✓ {tk}: {kpis} KPIs, {comm} commentary items "
                  f"-> cached {cp}")
            # Space out calls to avoid rate limits on back-to-back large PDFs.
            if ticker != args.tickers[-1]:
                time.sleep(args.api_sleep)

    if not extractions:
        print("Nothing extracted.")
        sys.exit(1)

    # Render each, with a shared peer block built from the whole set.
    for tk, data in extractions.items():
        if not args.no_peers:
            data["peers"] = build_peer_block(tk, extractions)
        out_pdf = os.path.join(OUT_DIR, f"{tk}_{quarter}.pdf")
        try:
            render_pdf(data, out_pdf)
            print(f"📄 {tk}: {out_pdf}")
        except Exception as e:
            print(f"✗ {tk}: render failed ({e}); extraction JSON is still cached at {_cache_path(tk, quarter)}")

    print(f"\nDone. PDFs + extraction JSON in ./{OUT_DIR}/")


if __name__ == "__main__":
    main()
