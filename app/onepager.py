"""
app/onepager.py — Institutional one-pager PDF generator.

Generates a single-page A4 PDF covering:
  - Company header (name, ticker, sector, CMP, date)
  - Snapshot strip (Mcap, P/E, P/B, ROE, NIM, GNPA, CRAR)
  - Financial highlights table (5Y P&L snapshot)
  - Key ratios grid
  - DCF valuation summary
  - Investment thesis bullets (3 bull / 3 bear)
  - Disclaimer footer

Uses ReportLab (pure Python, no system dependencies, runs on Railway).

Called by:  POST /api/companies/{ticker}/onepager
Returns:    PDF binary stream with Content-Type: application/pdf
"""
from __future__ import annotations
import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph,
    Spacer, HRFlowable, KeepTogether,
)
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.pdfgen import canvas

# ── Colour palette ────────────────────────────────────────────────────────────
INK       = colors.HexColor("#0a0907")
PANEL     = colors.HexColor("#181510")
GOLD      = colors.HexColor("#d4a93e")
GOLD_DARK = colors.HexColor("#a07d2c")
TEXT      = colors.HexColor("#dcd5c1")
DIM       = colors.HexColor("#857d65")
GREEN     = colors.HexColor("#5a8f5a")
RED       = colors.HexColor("#a85148")
LINE      = colors.HexColor("#2c2820")
WHITE     = colors.white

W, H = A4   # 595 × 842 pts

# ── Helper formatters ─────────────────────────────────────────────────────────
def fc(n, d=0):
    if n is None: return "—"
    if abs(n) >= 1e5: return f"{n/1e5:.2f} L Cr"
    return f"{n:,.{d}f}"

def fp(n, d=1):
    if n is None: return "—"
    return f"{n:.{d}f}%"

def finr(n, d=0):
    if n is None: return "—"
    return f"₹{n:,.{d}f}"

def fmult(n, d=2):
    if n is None: return "—"
    return f"{n:.{d}f}x"


# ── Canvas-based page builder ─────────────────────────────────────────────────

class OnePager:
    def __init__(self, co, market: dict, financials: dict, metrics: dict,
                 intrinsic: float | None, thesis: str | None):
        self.co         = co
        self.mkt        = market
        self.fins       = financials
        self.mets       = metrics
        self.intrinsic  = intrinsic
        self.thesis     = thesis
        self.buf        = io.BytesIO()

    def build(self) -> bytes:
        c = canvas.Canvas(self.buf, pagesize=A4)
        c.setTitle(f"{self.co.name} — Equity Research One-Pager")

        self._draw_background(c)
        y = self._draw_header(c, H - 10*mm)
        y = self._draw_snapshot(c, y)
        y = self._draw_financial_table(c, y)
        y = self._draw_ratio_grid(c, y)
        y = self._draw_valuation_box(c, y)
        y = self._draw_thesis_bullets(c, y)
        self._draw_footer(c)

        c.showPage()
        c.save()
        self.buf.seek(0)
        return self.buf.read()

    def _draw_background(self, c):
        c.setFillColor(INK)
        c.rect(0, 0, W, H, fill=1, stroke=0)

    def _draw_header(self, c, y) -> float:
        # Gold accent bar
        c.setFillColor(GOLD)
        c.rect(0, y - 2, W, 2, fill=1, stroke=0)

        y -= 2

        # Left: company name + metadata
        c.setFillColor(TEXT)
        c.setFont("Helvetica-Bold", 20)
        c.drawString(15*mm, y - 14*mm, self.co.name)

        c.setFont("Helvetica", 8)
        c.setFillColor(DIM)
        meta = f"{self.co.ticker}  ·  {self.co.sector}  ·  {getattr(self.co,'template_code','')}"
        c.drawString(15*mm, y - 19*mm, meta)

        # Right: price block
        price  = self.mkt.get("price", 0)
        chg    = self.mkt.get("chgPct", 0) or 0
        c.setFont("Helvetica-Bold", 22)
        c.setFillColor(TEXT)
        c.drawRightString(W - 15*mm, y - 12*mm, finr(price, 1))

        col = GREEN if chg >= 0 else RED
        c.setFillColor(col)
        c.setFont("Helvetica", 9)
        sign = "+" if chg >= 0 else ""
        c.drawRightString(W - 15*mm, y - 18*mm, f"{sign}{chg:.2f}%  (1D)")

        # Date
        c.setFillColor(DIM)
        c.setFont("Helvetica", 7.5)
        c.drawRightString(W - 15*mm, y - 23*mm, f"As of {datetime.today().strftime('%d %b %Y')}  ·  All figures ₹ Crore unless stated")

        # Separator
        y -= 28*mm
        c.setStrokeColor(LINE)
        c.setLineWidth(0.5)
        c.line(10*mm, y, W - 10*mm, y)
        return y - 3*mm

    def _draw_snapshot(self, c, y) -> float:
        """11-stat snapshot strip."""
        co   = self.co
        mkt  = self.mkt
        mets = self.mets
        iv   = self.intrinsic

        def get_m(key):
            for cat in (mets.get("categories") or []):
                for m in cat.get("metrics", []):
                    if m.get("key") == key:
                        v = m.get("value")
                        return v
            return None

        mcap = mkt.get("mcapCr") or (mkt.get("price",0) * getattr(co,"shares_outstanding",40))
        mos  = ((iv - mkt.get("price",0)) / mkt.get("price",1)) * 100 if iv else None

        stats = [
            ("Market Cap",     f"₹{fc(mcap)}"),
            ("P/E (TTM)",      fmult(get_m("pe_ratio")) if get_m("pe_ratio") else fmult(mkt.get("pe"))),
            ("P/B",            fmult(get_m("pb_ratio")) if get_m("pb_ratio") else fmult(mkt.get("pb"))),
            ("ROE",            fp(((get_m("roe") or 0) * 100)) if get_m("roe") else "—"),
            ("ROA",            fp(((get_m("roa") or 0) * 100)) if get_m("roa") else "—"),
            ("NIM",            fp(((get_m("nim_metric") or 0) * 100)) if get_m("nim_metric") else "—"),
            ("GNPA",           fp(((get_m("gnpa_pct") or 0) * 100), 2) if get_m("gnpa_pct") else "—"),
            ("CRAR",           fp(((get_m("crar_metric") or 0) * 100)) if get_m("crar_metric") else "—"),
            ("Intrinsic",      finr(iv) if iv else "—"),
            ("MoS",            f"{mos:+.1f}%" if mos is not None else "—"),
            ("Verdict",        "TRIM" if mos and mos < -10 else "ACCUMULATE" if mos and mos > 10 else "HOLD"),
        ]

        col_w = (W - 20*mm) / len(stats)
        x0    = 10*mm

        # Background strip
        c.setFillColor(PANEL)
        c.rect(x0, y - 14*mm, W - 20*mm, 14*mm, fill=1, stroke=0)

        for i, (label, value) in enumerate(stats):
            x = x0 + i * col_w + col_w / 2
            c.setFont("Helvetica", 6.5)
            c.setFillColor(DIM)
            c.drawCentredString(x, y - 5.5*mm, label.upper())

            # Color-code verdict and MoS
            if label == "Verdict":
                c.setFillColor(GREEN if "BUY" in value or "ACC" in value else RED if "AVOID" in value else GOLD)
            elif label == "MoS" and mos is not None:
                c.setFillColor(GREEN if mos > 0 else RED)
            elif label == "ROE":
                c.setFillColor(GREEN)
            else:
                c.setFillColor(TEXT)

            c.setFont("Helvetica-Bold", 8.5)
            c.drawCentredString(x, y - 11*mm, value)

        y -= 16*mm
        c.setStrokeColor(LINE)
        c.setLineWidth(0.3)
        c.line(10*mm, y, W - 10*mm, y)
        return y - 4*mm

    def _draw_financial_table(self, c, y) -> float:
        """5-year P&L snapshot table."""
        c.setFont("Helvetica-Bold", 8)
        c.setFillColor(GOLD)
        c.drawString(15*mm, y, "FINANCIAL HIGHLIGHTS")

        c.setFont("Helvetica", 7)
        c.setFillColor(DIM)
        c.drawRightString(W - 15*mm, y, "₹ Crore")

        y -= 3*mm

        fins = self.fins
        years = (fins.get("years") or fins.get("years_available") or [])[-5:]

        # Get P&L rows from available data
        stmts = fins.get("statements") or {}
        is_fin = fins.get("is_financial", True)

        if is_fin:
            rows_def = [
                ("Interest Income", "interest_income", "revenue"),
                ("Net Int. Income", "nii", None),
                ("Pre-prov Profit",  "ppop", None),
                ("Provisions",       "provisions", None),
                ("PAT",              "pat", None),
            ]
        else:
            rows_def = [
                ("Revenue",     "revenue", None),
                ("EBITDA",      "ebitda", None),
                ("EBIT",        "ebit",  None),
                ("PAT",         "pat",   None),
                ("OCF",         "operating_cf", None),
            ]

        # Build table data
        header = [""] + [str(y) for y in years]
        data   = [header]
        for label, key1, key2 in rows_def:
            row = [label]
            for yr in years:
                yr_stmts = stmts.get(yr) or stmts.get(str(yr)) or {}
                val = (yr_stmts.get("PL") or {}).get(key1)
                if val is None and key2:
                    val = (yr_stmts.get("PL") or {}).get(key2)
                if val is None:
                    val = (yr_stmts.get("CF") or {}).get(key1)
                row.append(fc(val) if val is not None else "—")
            data.append(row)

        col_w0 = 35*mm
        col_wn = (W - 20*mm - col_w0) / max(len(years), 1) if years else 20*mm
        col_ws = [col_w0] + [col_wn] * len(years)

        tbl = Table(data, colWidths=col_ws, rowHeights=5.5*mm)
        tbl.setStyle(TableStyle([
            ("BACKGROUND",   (0,0), (-1,0),  PANEL),
            ("TEXTCOLOR",    (0,0), (-1,0),  DIM),
            ("FONTNAME",     (0,0), (-1,0),  "Helvetica"),
            ("FONTSIZE",     (0,0), (-1,-1), 7.5),
            ("ALIGN",        (1,0), (-1,-1), "RIGHT"),
            ("ALIGN",        (0,0), (0,-1),  "LEFT"),
            ("TEXTCOLOR",    (0,1), (0,-1),  DIM),
            ("TEXTCOLOR",    (1,1), (-1,-1), TEXT),
            # Bold PAT row (last data row)
            ("FONTNAME",     (0,-1), (-1,-1), "Helvetica-Bold"),
            ("TEXTCOLOR",    (1,-1), (-1,-1), GOLD),
            ("LINEBELOW",    (0,0),  (-1,0),  0.3, LINE),
            ("LINEBELOW",    (0,-1), (-1,-1), 0.3, LINE),
            ("ROWBACKGROUNDS",(0,1), (-1,-1), [INK, PANEL]),
            ("LEFTPADDING",  (0,0),  (0,-1),  3),
            ("RIGHTPADDING", (-1,0), (-1,-1), 3),
        ]))

        tbl_h = (len(data)) * 5.5*mm
        tbl.wrapOn(c, W - 20*mm, tbl_h)
        tbl.drawOn(c, 10*mm, y - tbl_h)
        return y - tbl_h - 5*mm

    def _draw_ratio_grid(self, c, y) -> float:
        """2×4 key ratio grid."""
        co   = self.co
        mets = self.mets

        def get_m(key, formatted=True):
            for cat in (mets.get("categories") or []):
                for m in cat.get("metrics", []):
                    if m.get("key") == key:
                        return m.get("formatted") if formatted else m.get("value")
            return "—"

        c.setFont("Helvetica-Bold", 8)
        c.setFillColor(GOLD)
        c.drawString(15*mm, y, "KEY RATIOS")
        y -= 3*mm

        ratios = [
            ("PAT Growth YoY",   get_m("pat_growth_yoy")),
            ("PAT CAGR 3Y",      get_m("pat_cagr_3y")),
            ("ROE",              get_m("roe")),
            ("ROA",              get_m("roa")),
            ("NIM",              get_m("nim_metric")),
            ("GNPA",             get_m("gnpa_pct")),
            ("Cost-to-Income",   get_m("leverage_ratio")),
            ("EPS",              get_m("eps")),
            ("P/E",              get_m("pe_ratio")),
            ("P/B",              get_m("pb_ratio")),
            ("P/AUM",            get_m("p_aum")),
            ("Earnings Yield",   get_m("earnings_yield")),
        ]

        cols = 4
        col_w = (W - 20*mm) / cols
        row_h = 8*mm
        rows  = (len(ratios) + cols - 1) // cols

        for i, (label, value) in enumerate(ratios):
            col = i % cols
            row = i // cols
            x   = 10*mm + col * col_w
            yy  = y - row * row_h

            c.setFillColor(PANEL)
            c.rect(x + 0.5, yy - row_h + 0.5, col_w - 1, row_h - 1, fill=1, stroke=0)

            c.setFont("Helvetica", 6.5)
            c.setFillColor(DIM)
            c.drawString(x + 3, yy - 4*mm, label)

            c.setFont("Helvetica-Bold", 8.5)
            c.setFillColor(TEXT)
            c.drawString(x + 3, yy - 7.5*mm, value)

        return y - rows * row_h - 5*mm

    def _draw_valuation_box(self, c, y) -> float:
        """DCF valuation summary."""
        iv  = self.intrinsic
        mkt = self.mkt
        price = mkt.get("price", 0)
        mos   = ((iv - price) / price) * 100 if iv and price else None

        c.setFont("Helvetica-Bold", 8)
        c.setFillColor(GOLD)
        c.drawString(15*mm, y, "VALUATION SUMMARY")
        y -= 3*mm

        # Box background
        box_h = 18*mm
        c.setFillColor(PANEL)
        c.rect(10*mm, y - box_h, W - 20*mm, box_h, fill=1, stroke=0)
        c.setStrokeColor(GOLD_DARK)
        c.setLineWidth(0.5)
        c.rect(10*mm, y - box_h, W - 20*mm, box_h, fill=0, stroke=1)

        entries = [
            ("Methodology",     "Residual Income (NBFC) / FCFF DCF (non-fin)"),
            ("Cost of Equity",  "Ke = 7.10% + β × 8.50%  (Damodaran India ERP 2025)"),
            ("Blended Intrinsic", finr(iv) if iv else "—"),
            ("CMP",             finr(price, 1)),
            ("Margin of Safety", f"{mos:+.1f}%" if mos is not None else "—"),
        ]

        col_w = (W - 20*mm) / len(entries)
        for i, (label, value) in enumerate(entries):
            x = 10*mm + i * col_w + 2

            c.setFont("Helvetica", 6.5)
            c.setFillColor(DIM)
            c.drawString(x, y - 6*mm, label)

            if label == "Margin of Safety" and mos is not None:
                c.setFillColor(GREEN if mos > 0 else RED)
            elif label == "Blended Intrinsic":
                c.setFillColor(GOLD)
            else:
                c.setFillColor(TEXT)
            c.setFont("Helvetica-Bold", 8.5)
            c.drawString(x, y - 13*mm, value)

        return y - box_h - 5*mm

    def _draw_thesis_bullets(self, c, y) -> float:
        """Bull / Bear thesis bullets, or AI thesis snippet."""
        c.setFont("Helvetica-Bold", 8)
        c.setFillColor(GOLD)
        c.drawString(15*mm, y, "INVESTMENT THESIS")
        y -= 4*mm

        if self.thesis:
            # Use first 400 chars of AI thesis
            text = self.thesis[:500].replace("\n", " ").strip()
            lines = []
            while len(text) > 80:
                idx = text[:80].rfind(" ")
                lines.append(text[:idx])
                text = text[idx+1:]
            lines.append(text)

            c.setFont("Helvetica", 7.5)
            c.setFillColor(TEXT)
            for line in lines[:6]:
                c.drawString(15*mm, y, line)
                y -= 4*mm
        else:
            bullets = [
                ("▲", "Gold price tailwind (+22% YoY) and branch network density create structural moat in semi-urban segments."),
                ("▲", f"ROE {fp((self.mets or {}).get('roe_val', 30))} — top-decile NBFC profitability; CRAR 23.4% supports 15%+ AUM growth without dilution."),
                ("▲", "Mix shift to longer-tenor, higher-yield products (+50bps yield uplift); bank competitive entry moderated post-RBI scrutiny."),
                ("▼", "Gold price reversal >15% would compress LTV cushion and trigger auctions, pressuring AUM growth."),
                ("▼", "Microfinance subsidiary (Belstar) under stress; RBI cash disbursement curbs cap ticket sizes."),
                ("▼", "Promoter concentration >73% with succession risk; FY26 ROE likely peak — normalisation toward 22-25% expected."),
            ]
            for arrow, text in bullets:
                col = GREEN if arrow == "▲" else RED
                c.setFillColor(col)
                c.setFont("Helvetica-Bold", 8)
                c.drawString(15*mm, y, arrow)
                c.setFillColor(TEXT)
                c.setFont("Helvetica", 7.5)
                c.drawString(21*mm, y, text[:95])
                y -= 5*mm

        return y - 3*mm

    def _draw_footer(self, c):
        """Disclaimer footer."""
        y = 8*mm
        c.setStrokeColor(LINE)
        c.setLineWidth(0.3)
        c.line(10*mm, y + 4*mm, W - 10*mm, y + 4*mm)

        c.setFont("Helvetica", 6)
        c.setFillColor(DIM)
        c.drawString(15*mm, y + 1.5*mm,
            f"Equity Research Terminal v0.3  ·  Generated {datetime.today().strftime('%d %b %Y')}  "
            f"·  For educational purposes only  ·  Not SEBI-registered research advice  "
            f"·  Verify all figures against company filings before making investment decisions.")


def build_onepager(co, market: dict, financials: dict, metrics: dict,
                   intrinsic: float | None = None, thesis: str | None = None) -> bytes:
    """Entry point called by the FastAPI route."""
    op = OnePager(co, market, financials, metrics, intrinsic, thesis)
    return op.build()
