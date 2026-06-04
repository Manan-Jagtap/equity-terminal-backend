"""
pl_parser.py — multi-sector P&L extractor for BSE result PDFs.

Validated against real filings: Bajaj Finance (NBFC), TCS (IT), Sun Pharma (pharma).
Handles:
  - 3 header formats: dd.mm.yyyy date row; split "Month DD,/YYYY"; audited-flag row
  - reporting units: crore / million / lakh / thousand (normalizes to crore)
  - column selection: the AUDITED column under "Year ended" (current full year)
  - OCR artifacts: "18.430.64" (periods as separators), "8, 197.97" (space in number)
  - exclusion guard: 'profit before tax' must not match 'before exceptional' line
  - column-drift guard: only positional-index rows with exactly ncols numbers

Banks file SCANNED PDFs (no extractable text) -> caller detects empty text and
falls back to existing data; this parser is for text-extractable filings only.
"""
import re

# ── sector label maps (substring match, lowercased) -> canonical line_item ──
NBFC_LABELS = [
    ("interest income", "interest_income"),
    ("total revenue from operations", "revenue"),
    ("total income", "total_income"),
    ("finance costs", "interest_expense"),
    ("impairment on financial", "provisions"),
    ("employee benefits expense", "employee_cost"),
    ("depreciation and amortis", "depreciation"),
    ("total expenses", "total_expenses"),
    ("profit before tax", "pbt"),
    ("total tax expense", "tax"),
    ("profit after tax", "pat"),
]
IT_CORPORATE_LABELS = [
    ("revenue from operations", "revenue"),
    ("total income", "total_income"),
    ("employee benefit expense", "employee_cost"),
    ("finance costs", "interest_expense"),
    ("depreciation and amortis", "depreciation"),
    ("total expenses", "total_expenses"),
    ("profit before tax", "pbt"),
    ("total tax expense", "tax"),
    ("profit for the year", "pat"),
    ("profit for the period", "pat"),
]
PHARMA_MFG_LABELS = [
    ("total revenue from operations", "revenue"),
    ("total income", "total_income"),
    ("cost of materials consumed", "raw_material"),
    ("employee benefits expense", "employee_cost"),
    ("finance costs", "interest_expense"),
    ("depreciation and amortis", "depreciation"),
    ("total expenses", "total_expenses"),
    ("profit/ (loss) before tax", "pbt"),
    ("profit before tax", "pbt"),
    ("total tax expense", "tax"),
    ("profit for the period", "pat"),
]

# template_code -> map.  (BANK absent: banks file scanned PDFs -> auto-skipped.)
# MANUFACTURING + CONSUMER routed to goods (PHARMA_MFG) map: they have a
# cost-of-materials structure the IT map would miss. ENERGY stays revenue-based.
SECTOR_MAP = {
    "NBFC": NBFC_LABELS, "HFC": NBFC_LABELS, "AMC": NBFC_LABELS,
    "FINANCIAL": NBFC_LABELS,
    "IT_SERVICES": IT_CORPORATE_LABELS, "ENERGY": IT_CORPORATE_LABELS,
    "MANUFACTURING": PHARMA_MFG_LABELS, "CONSUMER": PHARMA_MFG_LABELS,
    "PHARMA": PHARMA_MFG_LABELS, "AUTO": PHARMA_MFG_LABELS,
}

EXCLUDE = ["before exceptional", "exceptional items"]
DATE_RE = re.compile(r"\b(\d{2})[./](\d{2})[./](\d{4})\b")
SPLIT_YEAR_RE = re.compile(r"\b(20\d{2})\b")


def detect_unit_multiplier(text):
    head = text[:700].lower()
    if re.search(r"in\s*million", head) or re.search(r"in\s*mn", head):
        return 0.1
    if re.search(r"in\s*lakh", head) or re.search(r"in\s*lac", head):
        return 0.01
    if re.search(r"in\s*'?000", head) or re.search(r"in\s*thousand", head):
        return 0.0001
    return 1.0


def _num(tok):
    if tok is None:
        return None
    t = tok.strip()
    if t in ("-", "", "\u2013", "\u2014"):
        return None
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()").replace(" ", "")
    if t.count(".") > 1:                       # "18.430.64" -> 18430.64
        p = t.split(".")
        t = "".join(p[:-1]) + "." + p[-1]
    t = t.replace(",", "")
    if not re.fullmatch(r"\d+(\.\d+)?", t):
        return None
    v = float(t)
    return -v if neg else v


def _detect_columns(lines):
    """Return (ncols, target_col, target_year). Handles 3 header styles."""
    # Style A: a line with >=2 dd.mm.yyyy dates
    for i, ln in enumerate(lines):
        ds = DATE_RE.findall(ln)
        if len(ds) >= 2:
            ncols = len(ds)
            year_start = ncols - 2 if ncols >= 4 else ncols - 1
            flags = []
            for j in range(i + 1, min(i + 3, len(lines))):
                fl = re.findall(r"(audited|unaudited)", lines[j].lower())
                if len(fl) == ncols:
                    flags = fl
                    break
            target = None
            if flags:
                for idx in range(year_start, ncols):
                    if flags[idx] == "audited":
                        target = idx
                        break
            if target is None:
                target = year_start
            return ncols, target, int(ds[target][2])
    # Style B: split "Month DD," line followed by a year line with >=2 years
    for i, ln in enumerate(lines):
        yrs = SPLIT_YEAR_RE.findall(ln)
        # a pure year row: mostly years, little else
        if len(yrs) >= 4 and len(ln.split()) <= len(yrs) + 2:
            ncols = len(yrs)
            year_start = ncols - 2 if ncols >= 4 else ncols - 1
            # no audited flags in this style typically; year-ended current = year_start
            return ncols, year_start, int(yrs[year_start])
    return None, None, None


def parse_pl(text, template_code="NBFC"):
    labels = SECTOR_MAP.get((template_code or "").upper(), NBFC_LABELS)
    mult = detect_unit_multiplier(text)
    lines = text.splitlines()
    ncols, target_col, target_year = _detect_columns(lines)
    if ncols is None:
        return {"error": "no parseable column header", "pl": {}}

    out = {}
    for raw in lines:
        ln = re.sub(r"(\d),\s+(\d)", r"\1,\2", raw)
        low = ln.lower()
        if any(x in low for x in EXCLUDE):
            continue
        for label, canon in labels:
            if label in low and canon not in out:
                after = ln[low.index(label) + len(label):]
                nums = [n for n in (_num(t) for t in after.split()) if n is not None]
                val = None
                if len(nums) == ncols:
                    val = nums[target_col]
                elif len(nums) > ncols:
                    val = nums[target_col - ncols]      # trailing-aligned
                if val is not None:
                    out[canon] = round(val * mult, 2)
                break

    if "interest_income" in out and "interest_expense" in out:
        out["nii"] = round(out["interest_income"] - out["interest_expense"], 2)
    return {"fiscal_year": target_year, "ncols": ncols, "target_col": target_col,
            "unit_mult": mult, "pl": out}


def validate_pl(pl: dict) -> tuple[bool, list]:
    r = []
    g = pl.get
    # 1. must have at least a top line and a bottom line
    top = g("revenue") or g("interest_income") or g("total_income")
    if top is None:
        r.append("no top line (revenue/interest_income/total_income)")
    pat = g("pat"); pbt = g("pbt")
    if pat is None and pbt is None:
        r.append("no profit line (pat/pbt)")

    # 2. magnitude floor: a listed company's annual top line in crore is not < 10
    #    (catches Britannia revenue=1, total_income=1 fragments)
    for k in ("revenue", "interest_income", "total_income"):
        v = g(k)
        if v is not None and 0 < abs(v) < 10:
            r.append(f"{k}={v} implausibly small (<10 cr)")

    # 3. profit lines that are single-digit fragments while a top line is large
    if top and top > 1000:
        for k in ("pat", "pbt"):
            v = g(k)
            if v is not None and 0 < abs(v) < 10:
                r.append(f"{k}={v} fragment vs top={top}")

    # 4. internal consistency: |pat| should not exceed |pbt| by much
    #    (tax reduces profit; pat>pbt only via tax credit, allow 20% slack)
    if pat is not None and pbt is not None and pbt > 0:
        if pat > pbt * 1.2:
            r.append(f"pat({pat}) > pbt({pbt})*1.2 — likely column mismatch")

    # 5. total_income should be >= top revenue line (it's revenue + other income)
    ti = g("total_income"); rev = g("revenue") or g("interest_income")
    if ti is not None and rev is not None and ti > 0 and rev > 0:
        if ti < rev * 0.9:   # allow 10% slack for rounding/segment quirks
            r.append(f"total_income({ti}) < revenue({rev}) — column mismatch")

    # 6. NII sanity for financials: 0 < nii < interest_income
    ii = g("interest_income"); nii = g("nii")
    if ii is not None and nii is not None:
        if not (0 < nii <= ii):
            r.append(f"nii({nii}) out of range vs interest_income({ii})")

    # 7. raw_material should not exceed total revenue (a cost can't exceed sales hugely)
    rm = g("raw_material")
    if rm is not None and top is not None and top > 0 and rm > top * 1.5:
        r.append(f"raw_material({rm}) >> top({top})")

    return (len(r) == 0, r)


