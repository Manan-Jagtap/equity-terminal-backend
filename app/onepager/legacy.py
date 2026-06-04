"""
app/onepager.py — Simplified robust PDF one-pager using ReportLab.
Uses only built-in fonts (Helvetica) to avoid font registration issues on Railway.
"""
from __future__ import annotations
import io
from datetime import datetime
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors

W, H = A4

INK   = colors.HexColor("#0a0907")
GOLD  = colors.HexColor("#d4a93e")
TEXT  = colors.HexColor("#dcd5c1")
DIM   = colors.HexColor("#857d65")
GREEN = colors.HexColor("#5a8f5a")
RED   = colors.HexColor("#a85148")
PANEL = colors.HexColor("#181510")
LINE  = colors.HexColor("#2c2820")
WHITE = colors.white


def _fc(n, d=0):
    if n is None: return "—"
    if abs(n) >= 1e5: return f"{n/1e5:.2f} L Cr"
    return f"{n:,.{d}f}"

def _fp(n, d=1):
    if n is None: return "—"
    return f"{float(n):.{d}f}%"

def _finr(n, d=0):
    if n is None: return "—"
    return f"Rs{float(n):,.{d}f}"


def build_onepager(co, market: dict, financials: dict, metrics: dict,
                   intrinsic: float | None = None, thesis: str | None = None) -> bytes:
    buf = io.BytesIO()
    c   = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"{co.name} — Equity Research One-Pager")

    # Background
    c.setFillColor(INK)
    c.rect(0, 0, W, H, fill=1, stroke=0)

    y = H - 8

    # Gold top bar
    c.setFillColor(GOLD)
    c.rect(0, y - 2, W, 2, fill=1, stroke=0)
    y -= 4

    # Company name
    c.setFillColor(TEXT)
    c.setFont("Helvetica-Bold", 22)
    c.drawString(15, y - 18, co.name)

    c.setFont("Helvetica", 8)
    c.setFillColor(DIM)
    c.drawString(15, y - 28, f"{co.ticker}  ·  {co.sector}  ·  {getattr(co,'template_code','')}")

    # Price
    price = market.get("price", 0) or 0
    chg   = market.get("chgPct", 0) or 0
    c.setFont("Helvetica-Bold", 24)
    c.setFillColor(TEXT)
    c.drawRightString(W - 15, y - 16, f"Rs{price:,.1f}")
    c.setFont("Helvetica", 9)
    c.setFillColor(GREEN if chg >= 0 else RED)
    c.drawRightString(W - 15, y - 26, f"{'+' if chg>=0 else ''}{chg:.2f}%")
    c.setFont("Helvetica", 7)
    c.setFillColor(DIM)
    c.drawRightString(W - 15, y - 34, f"As of {datetime.today().strftime('%d %b %Y')}  |  All figures Rs Crore")

    y -= 42

    # Separator
    c.setStrokeColor(LINE)
    c.setLineWidth(0.5)
    c.line(10, y, W - 10, y)
    y -= 4

    # Snapshot strip
    mcap = market.get("mcapCr") or (price * getattr(co, "shares_outstanding", 40))
    mos  = ((intrinsic - price) / price * 100) if intrinsic and price else None

    def get_m(key):
        for cat in (metrics.get("categories") or []):
            for m in cat.get("metrics", []):
                if m.get("key") == key:
                    return m.get("value")
        return None

    stats = [
        ("Market Cap",    f"Rs{_fc(mcap)}"),
        ("P/E (TTM)",     f"{(get_m('pe_ratio') or 0):.1f}x" if get_m('pe_ratio') else "—"),
        ("P/B",           f"{(get_m('pb_ratio') or 0):.2f}x" if get_m('pb_ratio') else "—"),
        ("ROE",           _fp((get_m('roe') or 0)*100) if get_m('roe') else "—"),
        ("ROA",           _fp((get_m('roa') or 0)*100) if get_m('roa') else "—"),
        ("NIM",           _fp((get_m('nim_metric') or 0)*100) if get_m('nim_metric') else "—"),
        ("GNPA",          _fp((get_m('gnpa_pct') or 0)*100, 2) if get_m('gnpa_pct') else "—"),
        ("CRAR",          _fp((get_m('crar_metric') or 0)*100) if get_m('crar_metric') else "—"),
        ("Intrinsic",     _finr(intrinsic) if intrinsic else "—"),
        ("MoS",           f"{mos:+.1f}%" if mos is not None else "—"),
        ("Verdict",       "TRIM" if mos and mos < -10 else "ACCUM" if mos and mos > 10 else "HOLD"),
    ]

    strip_h = 16
    col_w   = (W - 20) / len(stats)
    c.setFillColor(PANEL)
    c.rect(10, y - strip_h, W - 20, strip_h, fill=1, stroke=0)

    for i, (label, value) in enumerate(stats):
        x = 10 + i * col_w + col_w / 2
        c.setFont("Helvetica", 6)
        c.setFillColor(DIM)
        c.drawCentredString(x, y - 6, label.upper())
        if label in ("Verdict","MoS","ROE"):
            c.setFillColor(GREEN if (label=="ROE" or (mos and mos>0)) else RED)
        else:
            c.setFillColor(TEXT)
        c.setFont("Helvetica-Bold", 8)
        c.drawCentredString(x, y - 13, value)

    y -= strip_h + 6
    c.setStrokeColor(LINE); c.setLineWidth(0.3)
    c.line(10, y, W - 10, y)
    y -= 8

    # Financials table
    c.setFont("Helvetica-Bold", 8)
    c.setFillColor(GOLD)
    c.drawString(15, y, "FINANCIAL HIGHLIGHTS")
    c.setFont("Helvetica", 7); c.setFillColor(DIM)
    c.drawRightString(W - 15, y, "Rs Crore")
    y -= 4

    stmts    = financials.get("statements") or {}
    years    = sorted(stmts.keys())[-5:]
    is_fin   = financials.get("is_financial", True)

    if is_fin:
        rows_def = [("Interest Income","interest_income","revenue"),
                    ("NII","nii",None),("Provisions","provisions",None),
                    ("PAT","pat",None)]
    else:
        rows_def = [("Revenue","revenue",None),("EBITDA","ebitda",None),
                    ("EBIT","ebit",None),("PAT","pat",None)]

    col0 = 38; coln = (W - 20 - col0) / max(len(years), 1) if years else 25
    row_h = 7

    # Header
    c.setFillColor(PANEL)
    c.rect(10, y - row_h, W - 20, row_h, fill=1, stroke=0)
    c.setFont("Helvetica", 6); c.setFillColor(DIM)
    c.drawString(13, y - 5, "")
    for j, yr in enumerate(years):
        c.drawRightString(10 + col0 + (j+1)*coln - 2, y - 5, str(yr))
    y -= row_h

    for label, k1, k2 in rows_def:
        is_bold = label == "PAT"
        bg = colors.HexColor("#211d17") if is_bold else INK
        c.setFillColor(bg)
        c.rect(10, y - row_h, W - 20, row_h, fill=1, stroke=0)
        c.setFont("Helvetica-Bold" if is_bold else "Helvetica", 7)
        c.setFillColor(TEXT if is_bold else DIM)
        c.drawString(13, y - 5, label)
        for j, yr in enumerate(years):
            pl  = (stmts.get(yr) or stmts.get(str(yr)) or {}).get("PL", {})
            val = pl.get(k1) or (pl.get(k2) if k2 else None)
            c.setFillColor(GOLD if (is_bold and j == len(years)-1) else TEXT)
            c.drawRightString(10 + col0 + (j+1)*coln - 2, y - 5, _fc(val) if val is not None else "—")
        y -= row_h

    c.setStrokeColor(LINE); c.line(10, y, W - 10, y)
    y -= 8

    # Key ratios grid
    c.setFont("Helvetica-Bold", 8); c.setFillColor(GOLD)
    c.drawString(15, y, "KEY RATIOS")
    y -= 4

    ratios = [
        ("PAT Growth YoY", get_m("pat_growth_yoy"), lambda v: f"{v*100:+.1f}%"),
        ("PAT CAGR 3Y",    get_m("pat_cagr_3y"),    lambda v: f"{v*100:.1f}%"),
        ("ROE",            get_m("roe"),             lambda v: f"{v*100:.1f}%"),
        ("ROA",            get_m("roa"),             lambda v: f"{v*100:.1f}%"),
        ("NIM",            get_m("nim_metric"),      lambda v: f"{v*100:.1f}%"),
        ("GNPA",           get_m("gnpa_pct"),        lambda v: f"{v*100:.2f}%"),
        ("EPS",            get_m("eps"),             lambda v: f"Rs{v:.1f}"),
        ("P/E",            get_m("pe_ratio"),        lambda v: f"{v:.1f}x"),
        ("P/B",            get_m("pb_ratio"),        lambda v: f"{v:.2f}x"),
        ("P/AUM",          get_m("p_aum"),           lambda v: f"{v*100:.1f}%"),
        ("Earnings Yield", get_m("earnings_yield"),  lambda v: f"{v*100:.1f}%"),
        ("Leverage",       get_m("leverage_ratio"),  lambda v: f"{v:.2f}x"),
    ]

    cols = 4; col_rw = (W - 20) / cols; rrow_h = 9
    for i, (label, val, fmt) in enumerate(ratios):
        col = i % cols; row = i // cols
        x   = 10 + col * col_rw; yy = y - row * rrow_h
        c.setFillColor(PANEL)
        c.rect(x + 0.5, yy - rrow_h + 0.5, col_rw - 1, rrow_h - 1, fill=1, stroke=0)
        c.setFont("Helvetica", 6); c.setFillColor(DIM)
        c.drawString(x + 2, yy - 4, label)
        c.setFont("Helvetica-Bold", 7.5); c.setFillColor(TEXT)
        try:
            display = fmt(float(val)) if val is not None else "—"
        except Exception:
            display = "—"
        c.drawString(x + 2, yy - 8, display)

    y -= ((len(ratios) + cols - 1) // cols) * rrow_h + 6
    c.setStrokeColor(LINE); c.line(10, y, W - 10, y)
    y -= 8

    # Valuation box
    c.setFont("Helvetica-Bold", 8); c.setFillColor(GOLD)
    c.drawString(15, y, "VALUATION SUMMARY")
    y -= 4
    box_h = 18
    c.setFillColor(PANEL)
    c.rect(10, y - box_h, W - 20, box_h, fill=1, stroke=0)
    c.setStrokeColor(colors.HexColor("#a07d2c")); c.setLineWidth(0.5)
    c.rect(10, y - box_h, W - 20, box_h, fill=0, stroke=1)

    entries = [
        ("Model", "Residual Income (NBFC) / FCFF DCF"),
        ("Ke/WACC", "Rf=7.1% + β × ERP=8.5% (Damodaran India 2025)"),
        ("Intrinsic", _finr(intrinsic) if intrinsic else "—"),
        ("CMP", f"Rs{price:,.1f}"),
        ("MoS", f"{mos:+.1f}%" if mos is not None else "—"),
    ]
    ew = (W - 20) / len(entries)
    for i, (label, value) in enumerate(entries):
        x = 10 + i * ew + 2
        c.setFont("Helvetica", 6); c.setFillColor(DIM)
        c.drawString(x, y - 6, label)
        c.setFillColor(GOLD if label == "Intrinsic" else (GREEN if (label=="MoS" and mos and mos>0) else (RED if (label=="MoS" and mos and mos<0) else TEXT)))
        c.setFont("Helvetica-Bold", 8)
        c.drawString(x, y - 13, value[:20])

    y -= box_h + 6
    c.setStrokeColor(LINE); c.line(10, y, W - 10, y)
    y -= 8

    # Thesis / bullets
    c.setFont("Helvetica-Bold", 8); c.setFillColor(GOLD)
    c.drawString(15, y, "INVESTMENT THESIS")
    y -= 5

    if thesis:
        text = thesis[:400].replace("\n", " ")
        words = text.split()
        line, lines = [], []
        for w in words:
            line.append(w)
            if len(" ".join(line)) > 90:
                lines.append(" ".join(line[:-1]))
                line = [w]
        if line: lines.append(" ".join(line))
        c.setFont("Helvetica", 7.5); c.setFillColor(TEXT)
        for ln in lines[:6]:
            c.drawString(15, y, ln); y -= 5
    else:
        bullets = [
            ("^", "Gold price +22% YoY drove LTV expansion; 4,869 branch network provides structural moat."),
            ("^", "ROE 33.9%, CRAR 23.4% — top-decile NBFC; mix shift to longer-tenor products +50bps yield."),
            ("v", "Gold price reversal >15% would compress LTV cushion and trigger forced auctions."),
            ("v", "FY26 ROE likely peak; normalisation toward 22-25% expected in FY27E."),
        ]
        for arrow, text in bullets:
            c.setFillColor(GREEN if arrow == "^" else RED)
            c.setFont("Helvetica-Bold", 8); c.drawString(15, y, arrow)
            c.setFillColor(TEXT); c.setFont("Helvetica", 7.5)
            c.drawString(22, y, text[:95]); y -= 5

    # Footer
    c.setStrokeColor(LINE); c.setLineWidth(0.3)
    c.line(10, 12, W - 10, 12)
    c.setFont("Helvetica", 6); c.setFillColor(DIM)
    c.drawString(15, 6, f"Equity Terminal v0.3  ·  {datetime.today().strftime('%d %b %Y')}  ·  Educational purposes only  ·  Not SEBI-registered advice")

    c.showPage()
    c.save()
    buf.seek(0)
    return buf.read()
