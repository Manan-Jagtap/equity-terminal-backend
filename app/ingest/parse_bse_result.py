"""
parse_bse_result.py — PROOF OF CONCEPT for the BSE result-PDF pipeline.

Full chain for ONE ticker:
  scrip code -> latest Result filing -> download PDF (AttachHis/Live) ->
  locate P&L page -> column-aware parse -> print canonical line items.

This proves Phases 0-3 end to end before scaling to all 500.

Run:
  python -m app.ingest.parse_bse_result --ticker BAJFINANCE
"""
import os, sys, re, argparse
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import requests
from bse import BSE

try:
    import pdfplumber
except ImportError:
    print("ERROR: pip install pdfplumber"); sys.exit(1)

H = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
     "Referer": "https://www.bseindia.com/"}

NBFC_LABELS = [
    ("interest income",               "interest_income"),
    ("total revenue from operations", "revenue"),
    ("total income",                  "total_income"),
    ("finance costs",                 "interest_expense"),
    ("impairment on financial",       "provisions"),
    ("employee benefits expense",     "employee_cost"),
    ("depreciation and amortis",      "depreciation"),
    ("total expenses",                "total_expenses"),
    ("profit before tax",             "pbt"),
    ("total tax expense",             "tax"),
    ("profit after tax",              "pat"),
]
DATE_RE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})\b")
PL_MARKERS = ["interest income", "finance cost", "impairment",
              "profit before tax", "profit for the period", "total income"]


def _num(tok):
    if tok is None: return None
    t = tok.strip()
    if t in ("-", "", "–", "—"): return None
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()").replace(" ", "")
    # OCR artifact: thousands separators misread as '.' -> "18.430.64".
    # Real format has ONE decimal point; collapse all but the last dot.
    if t.count(".") > 1:
        p = t.split("."); t = "".join(p[:-1]) + "." + p[-1]
    t = t.replace(",", "")
    if not re.fullmatch(r"\d+(\.\d+)?", t): return None
    v = float(t)
    return -v if neg else v


def parse_pl(text):
    lines = text.splitlines()
    hdr_idx, dates = None, []
    for i, ln in enumerate(lines):
        ds = DATE_RE.findall(ln)
        if len(ds) >= 2:
            hdr_idx, dates = i, ds; break
    if hdr_idx is None:
        return {"error": "no date header"}
    ncols = len(dates)
    flagline = ""
    for j in range(hdr_idx+1, min(hdr_idx+3, len(lines))):
        if "audited" in lines[j].lower():
            flagline = lines[j]; break
    flags = re.findall(r"\((unaudited|audited)\)", flagline.lower())
    target_col = None
    if flags and len(flags) == ncols:
        for idx, f in enumerate(flags):
            if f == "audited":
                target_col = idx; break
    if target_col is None:
        target_col = ncols - 2 if ncols >= 2 else ncols - 1
    target_year = int(dates[target_col][2])
    out = {}
    skipped = []
    for raw in lines:
        ln = re.sub(r"(\d),\s+(\d)", r"\1,\2", raw)
        low = ln.lower()
        for label, canon in NBFC_LABELS:
            if label in low and canon not in out:
                after = ln[low.index(label)+len(label):]
                nums = [n for n in (_num(t) for t in after.split()) if n is not None]
                # Only trust positional column indexing when the row yields exactly
                # ncols numbers. A short row means a token mis-parsed and columns
                # would be misaligned -> skip rather than record a wrong value.
                if len(nums) == ncols:
                    out[canon] = nums[target_col]
                elif len(nums) > ncols:
                    # extra leading tokens (e.g. sub-item numbering); take from the right
                    out[canon] = nums[target_col - ncols]
                else:
                    skipped.append((canon, len(nums)))
                break
    if "interest_income" in out and "interest_expense" in out:
        out["nii"] = round(out["interest_income"] - out["interest_expense"], 2)
    return {"fiscal_year": target_year, "ncols": ncols, "target_col": target_col,
            "pl": out, "skipped": skipped}


def fetch_pdf(att):
    for base in ("AttachHis", "AttachLive"):
        url = f"https://www.bseindia.com/xml-data/corpfiling/{base}/{att}"
        try:
            r = requests.get(url, headers=H, timeout=40)
            if r.status_code == 200 and len(r.content) > 20000:
                return r.content
        except Exception:
            pass
    return None


def run(ticker):
    bse = BSE(download_folder="/tmp")
    try:
        scrip = bse.getScripCode(ticker.lower())
        print(f"{ticker}: scrip={scrip}")
        if not scrip:
            print("no scrip code"); return
        to_d = datetime.now()
        from_d = to_d - timedelta(days=420)
        data = bse.announcements(scripcode=str(scrip), from_date=from_d,
                                 to_date=to_d, category="Result")
        rows = data.get("Table", [])
        print(f"result filings found: {len(rows)}")
        audited = [r for r in rows if "audited" in r.get("NEWSSUB","").lower()
                   and "unaudited" not in r.get("NEWSSUB","").lower()]
        target = (audited or rows)
        if not target:
            print("no result filings"); return
        for cand in target[:3]:
            att = cand.get("ATTACHMENTNAME")
            print(f"trying: {cand.get('NEWSSUB','')[:60]} | {att}")
            pdf = fetch_pdf(att)
            if not pdf:
                print("  fetch failed, next"); continue
            with pdfplumber.open(__import__("io").BytesIO(pdf)) as doc:
                best, best_score = None, 0
                for p in doc.pages:
                    t = p.extract_text() or ""
                    score = sum(m in t.lower() for m in PL_MARKERS)
                    if score > best_score:
                        best, best_score = t, score
                if best_score >= 3:
                    res = parse_pl(best)
                    import json
                    print(json.dumps(res, indent=2))
                    return
                else:
                    print(f"  no P&L page (best score {best_score}), next")
        print("could not locate P&L in candidate filings")
    finally:
        try: bse.exit()
        except Exception: pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="BAJFINANCE")
    run(ap.parse_args().ticker)
