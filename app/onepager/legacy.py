"""
app/onepager.py — institutional one-page equity brief (ReportLab, LIGHT theme).

A clean, printable research sheet: header + verdict, a metric snapshot, the
5-year P&L, key ratios, the per-stock scorecard, the valuation summary, and an
honest bull / watch thesis derived from THIS company's own scorecard flags
(never hardcoded). Built-in Helvetica only, so it renders identically on
Railway. Every figure traces to the same engine the app shows.
"""
from __future__ import annotations
import io
from datetime import datetime
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors

W, H = A4
M = 34                      # page margin

# ── light palette ────────────────────────────────────────────────────────────
PAPER = colors.white
INK   = colors.HexColor("#141719")     # near-black text
HEAD  = colors.HexColor("#0f766e")     # deep teal accent (section heads, name)
MUTE  = colors.HexColor("#6b7280")     # grey labels
FAINT = colors.HexColor("#9aa2ad")
GREEN = colors.HexColor("#15803d")
RED   = colors.HexColor("#b91c1c")
AMBER = colors.HexColor("#b45309")
PANEL = colors.HexColor("#f4f6f7")     # light strip fill
PANEL2= colors.HexColor("#eef1f2")
LINE  = colors.HexColor("#e3e7ea")


def _fc(n, d=0):
    if n is None:
        return "—"
    if abs(n) >= 1e5:
        return f"{n/1e5:,.2f}L"
    return f"{n:,.{d}f}"


def _pct(n, d=1):
    return "—" if n is None else f"{float(n):.{d}f}%"


def _verdict_color(v):
    v = (v or "").upper()
    if v in ("BUY", "ACCUMULATE", "ACCUM"):
        return GREEN
    if v in ("SELL", "REDUCE", "TRIM"):
        return RED
    return AMBER


def _thesis(co, rec, metrics, scorecard):
    """Honest bull / watch bullets for THIS name — from the scorecard's own
    green/red flags plus the model verdict. Never hardcoded per-sector text."""
    sc = scorecard or {}
    bulls = [("+", g) for g in (sc.get("green_flags") or [])][:4]
    risks = [("!", r) for r in (sc.get("red_flags") or [])][:4]
    mos = (rec or {}).get("mos")
    verdict = (rec or {}).get("verdict")
    if mos is not None and not any("margin of safety" in b[1].lower() for b in bulls):
        line = f"Model verdict {verdict}: {mos*100:+.0f}% margin of safety vs the independent fair value."
        (bulls if mos > 0 else risks).insert(0, ("+" if mos > 0 else "!", line))
    if not bulls and not risks:
        bulls = [("+", "Valuation, quality and momentum read within normal ranges — no structural flags.")]
    return bulls, risks


def build_onepager(co, market: dict, financials: dict, metrics: dict,
                   intrinsic: float | None = None, thesis: str | None = None,
                   rec: dict | None = None, scorecard: dict | None = None) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"{co.name} — Equity Research One-Pager")
    c.setFillColor(PAPER); c.rect(0, 0, W, H, fill=1, stroke=0)

    price = market.get("price", 0) or 0
    chg = market.get("chgPct", 0) or 0
    mos = ((intrinsic - price) / price * 100) if intrinsic and price else None
    verdict = (rec or {}).get("verdict") or ("ACCUM" if mos and mos > 10 else "REDUCE" if mos and mos < -10 else "HOLD")

    def get_m(key):
        for cat in (metrics.get("categories") or []):
            for m in cat.get("metrics", []):
                if m.get("key") == key:
                    return m.get("value")
        return None

    def head(y, title, x=M, x2=W - M):
        c.setFillColor(HEAD); c.setFont("Helvetica-Bold", 8.5)
        c.drawString(x, y, title.upper())
        c.setStrokeColor(LINE); c.setLineWidth(0.6); c.line(x, y - 4, x2, y - 4)
        return y - 15

    # ── Header ───────────────────────────────────────────────────────────────
    y = H - M
    c.setFillColor(HEAD); c.rect(0, H - 4, W, 4, fill=1, stroke=0)
    c.setFillColor(INK); c.setFont("Helvetica-Bold", 21)
    c.drawString(M, y - 16, co.name[:44])
    c.setFillColor(MUTE); c.setFont("Helvetica", 8.5)
    cap = "Large Cap" if (market.get("mcapCr") or 0) >= 67000 else "Mid Cap" if (market.get("mcapCr") or 0) >= 22000 else "Small Cap"
    c.drawString(M, y - 28, f"{co.ticker}   ·   {co.sector}   ·   {cap}")
    c.setFillColor(INK); c.setFont("Helvetica-Bold", 22)
    c.drawRightString(W - M, y - 15, f"Rs {price:,.1f}")
    c.setFillColor(GREEN if chg >= 0 else RED); c.setFont("Helvetica", 9)
    c.drawRightString(W - M, y - 27, f"{'+' if chg >= 0 else ''}{chg:.2f}%")
    c.setFillColor(FAINT); c.setFont("Helvetica", 6.5)
    c.drawRightString(W - M, y - 36, f"As of {datetime.today().strftime('%d %b %Y')}  ·  Rs Crore")
    y -= 46

    # ── Verdict band ─────────────────────────────────────────────────────────
    bh = 34
    c.setFillColor(PANEL); c.roundRect(M, y - bh, W - 2 * M, bh, 5, fill=1, stroke=0)
    vc = _verdict_color(verdict)
    c.setFillColor(vc); c.setFont("Helvetica-Bold", 17)
    c.drawString(M + 12, y - 22, verdict.upper())
    cells = [
        ("FAIR VALUE", f"Rs {intrinsic:,.0f}" if intrinsic else "—", INK),
        ("MARGIN OF SAFETY", f"{mos:+.1f}%" if mos is not None else "—", GREEN if (mos or 0) > 0 else RED),
        ("COMPOSITE", f"{(rec or {}).get('composite'):.0f}/100" if (rec or {}).get("composite") is not None else "—", INK),
        ("SCORECARD", f"{scorecard.get('grade')} · {scorecard.get('overall'):.0f}" if (scorecard or {}).get("overall") is not None else "—", INK),
    ]
    cw = (W - 2 * M - 130) / len(cells)
    for i, (lb, vl, col) in enumerate(cells):
        x = M + 130 + i * cw
        c.setFillColor(MUTE); c.setFont("Helvetica", 6.2); c.drawString(x, y - 11, lb)
        c.setFillColor(col); c.setFont("Helvetica-Bold", 12); c.drawString(x, y - 25, vl)
    y -= bh + 14

    # ── Metric snapshot ──────────────────────────────────────────────────────
    is_fin = financials.get("is_financial", False)
    snap = [("Mkt Cap", f"Rs {_fc(market.get('mcapCr'))}"),
            ("P/E", f"{get_m('pe_ratio'):.1f}x" if get_m('pe_ratio') else "—"),
            ("P/B", f"{get_m('pb_ratio'):.2f}x" if get_m('pb_ratio') else "—"),
            ("ROE", _pct((get_m('roe') or 0) * 100) if get_m('roe') else "—"),
            ("ROA", _pct((get_m('roa') or 0) * 100) if get_m('roa') else "—")]
    snap += ([("NIM", _pct((get_m('nim_metric') or 0) * 100) if get_m('nim_metric') else "—"),
              ("GNPA", _pct((get_m('gnpa_pct') or 0) * 100, 2) if get_m('gnpa_pct') else "—")]
             if is_fin else
             [("EBITDA %", _pct((get_m('ebitda_margin') or 0) * 100) if get_m('ebitda_margin') else "—"),
              ("D/E", f"{get_m('leverage_ratio'):.2f}x" if get_m('leverage_ratio') else "—")])
    sh = 26
    c.setFillColor(PANEL2); c.roundRect(M, y - sh, W - 2 * M, sh, 4, fill=1, stroke=0)
    scw = (W - 2 * M) / len(snap)
    for i, (lb, vl) in enumerate(snap):
        x = M + i * scw + scw / 2
        c.setFillColor(MUTE); c.setFont("Helvetica", 6.2); c.drawCentredString(x, y - 10, lb.upper())
        c.setFillColor(INK); c.setFont("Helvetica-Bold", 10); c.drawCentredString(x, y - 21, vl)
        if i:
            c.setStrokeColor(LINE); c.setLineWidth(0.4); c.line(M + i * scw, y - sh + 5, M + i * scw, y - 5)
    y -= sh + 16

    # ── Two columns: left = P&L + ratios, right = scorecard + valuation ──────
    colL, colR = M, W / 2 + 8
    halfW = W / 2 - M - 8
    yL = yR = y

    # LEFT — financial highlights
    yL = head(yL, "Financial Highlights", x=colL, x2=colL + halfW)
    stmts = financials.get("statements") or {}
    years = sorted(stmts.keys())[-5:]
    rowsdef = ([("Interest Inc.", "interest_income", "revenue"), ("NII", "nii", None),
                ("PAT", "pat", None)] if is_fin else
               [("Revenue", "revenue", None), ("EBITDA", "ebitda", None),
                ("EBIT", "ebit", None), ("PAT", "pat", None)])
    yc = halfW / (len(years) + 1.4)
    c.setFont("Helvetica", 6); c.setFillColor(MUTE)
    for j, yr in enumerate(reversed(years)):
        c.drawRightString(colL + halfW - j * yc, yL, f"FY{str(yr)[-2:]}")
    yL -= 10
    for lbl, k1, k2 in rowsdef:
        bold = lbl == "PAT"
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 7.5)
        c.setFillColor(INK if bold else MUTE); c.drawString(colL, yL, lbl)
        for j, yr in enumerate(reversed(years)):
            pl = (stmts.get(yr) or stmts.get(str(yr)) or {}).get("PL", {})
            val = pl.get(k1) or (pl.get(k2) if k2 else None)
            c.setFillColor(HEAD if (bold and j == 0) else INK)
            c.setFont("Helvetica-Bold" if bold else "Helvetica", 7.5)
            c.drawRightString(colL + halfW - j * yc, yL, _fc(val))
        yL -= 11
    yL -= 6

    # LEFT — key ratios grid (2 cols)
    yL = head(yL, "Key Ratios", x=colL, x2=colL + halfW)
    ratios = [("PAT CAGR 3Y", get_m("pat_cagr_3y"), lambda v: f"{v*100:.1f}%"),
              ("PAT Growth", get_m("pat_growth_yoy"), lambda v: f"{v*100:+.1f}%"),
              ("ROCE", get_m("roce"), lambda v: f"{v*100:.1f}%"),
              ("EPS", get_m("eps"), lambda v: f"Rs {v:.1f}"),
              ("Earnings Yld", get_m("earnings_yield"), lambda v: f"{v*100:.1f}%"),
              ("Div Yield", get_m("dividend_yield"), lambda v: f"{v*100:.1f}%")]
    gw = halfW / 2
    for i, (lb, val, fmt) in enumerate(ratios):
        col, row = i % 2, i // 2
        x = colL + col * gw; yy = yL - row * 13
        try:
            disp = fmt(float(val)) if val is not None else "—"
        except Exception:
            disp = "—"
        c.setFillColor(MUTE); c.setFont("Helvetica", 6.5); c.drawString(x, yy, lb)
        c.setFillColor(INK); c.setFont("Helvetica-Bold", 8); c.drawString(x + gw - 40, yy, disp)
    yL -= ((len(ratios) + 1) // 2) * 13 + 4

    # RIGHT — scorecard bars
    if scorecard and scorecard.get("scores"):
        yR = head(yR, "Stock Scorecard", x=colR, x2=colR + halfW)
        for key in ("valuation", "quality", "growth", "momentum", "safety"):
            s = (scorecard["scores"].get(key) or {}).get("value")
            c.setFillColor(MUTE); c.setFont("Helvetica", 7); c.drawString(colR, yR, key.title())
            c.setFillColor(INK); c.setFont("Helvetica-Bold", 7.5)
            c.drawRightString(colR + halfW, yR, "—" if s is None else str(int(s)))
            bx, bw = colR + 58, halfW - 78
            c.setFillColor(LINE); c.roundRect(bx, yR - 1, bw, 3.4, 1.5, fill=1, stroke=0)
            if s is not None:
                col = GREEN if s >= 70 else AMBER if s >= 45 else RED
                c.setFillColor(col); c.roundRect(bx, yR - 1, bw * min(s, 100) / 100, 3.4, 1.5, fill=1, stroke=0)
            yR -= 13
        yR -= 4

    # RIGHT — valuation summary
    yR = head(yR, "Valuation", x=colR, x2=colR + halfW)
    vrows = [("Method", (rec or {}).get("primary_method") or "FCFF DCF / Residual Income"),
             ("Discount rate", "Ke = Rf(10Y G-sec) + β·ERP"),
             ("Fair value", f"Rs {intrinsic:,.0f}" if intrinsic else "—"),
             ("Current price", f"Rs {price:,.1f}"),
             ("Margin of safety", f"{mos:+.1f}%" if mos is not None else "—")]
    for lb, vl in vrows:
        c.setFillColor(MUTE); c.setFont("Helvetica", 7); c.drawString(colR, yR, lb)
        col = HEAD if lb == "Fair value" else (GREEN if (lb == "Margin of safety" and (mos or 0) > 0) else RED if lb == "Margin of safety" else INK)
        c.setFillColor(col); c.setFont("Helvetica-Bold", 7.5); c.drawRightString(colR + halfW, yR, str(vl)[:34])
        yR -= 12

    # ── Thesis (full width, below the columns) ───────────────────────────────
    y = min(yL, yR) - 8
    y = head(y, "Investment Thesis")
    bulls, risks = _thesis(co, rec, metrics, scorecard)

    def wrap(text, maxc=112):
        words, line, out = text.split(), [], []
        for w in words:
            line.append(w)
            if len(" ".join(line)) > maxc:
                out.append(" ".join(line[:-1])); line = [w]
        if line:
            out.append(" ".join(line))
        return out

    for arrow, text in (bulls + risks):
        col = GREEN if arrow == "+" else RED
        for k, ln in enumerate(wrap(text)):
            c.setFillColor(col); c.setFont("Helvetica-Bold", 8)
            if k == 0:
                c.drawString(M, y, arrow)
            c.setFillColor(INK); c.setFont("Helvetica", 8)
            c.drawString(M + 10, y, ln)
            y -= 10.5
        y -= 1.5

    # ── Footer ───────────────────────────────────────────────────────────────
    c.setStrokeColor(LINE); c.setLineWidth(0.5); c.line(M, 26, W - M, 26)
    c.setFillColor(FAINT); c.setFont("Helvetica", 6.3)
    c.drawString(M, 18, "Equity Terminal — independent, evidence-based research. Educational use only; not SEBI-registered investment advice.")
    c.drawRightString(W - M, 18, datetime.today().strftime("%d %b %Y"))

    c.showPage(); c.save(); buf.seek(0)
    return buf.read()
