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
                   rec: dict | None = None, scorecard: dict | None = None,
                   assumptions: dict | None = None, analyst: dict | None = None,
                   peers: list | None = None, description: str | None = None,
                   quarter: dict | None = None, cagr: dict | None = None,
                   concall: dict | None = None) -> bytes:
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

    # ── Business overview (full width, if we have a real description) ─────────
    def wrap_at(text, maxc):
        words, line, out = str(text).split(), [], []
        for w in words:
            line.append(w)
            if len(" ".join(line)) > maxc:
                out.append(" ".join(line[:-1])); line = [w]
        if line:
            out.append(" ".join(line))
        return out

    if description and len(description.strip()) > 40:
        y = head(y, "Business Overview")
        c.setFillColor(INK); c.setFont("Helvetica", 7.6)
        for ln in wrap_at(description.strip(), 150)[:3]:
            c.drawString(M, y, ln); y -= 10
        y -= 6

    # ── Two columns ──────────────────────────────────────────────────────────
    colL, colR = M, W / 2 + 8
    halfW = W / 2 - M - 8
    yL = yR = y
    stmts = financials.get("statements") or {}
    years = sorted(stmts.keys())[-5:]
    a = assumptions or {}
    v = (rec or {}).get("valuation") or {}

    def sec(section, key, yr=None):
        yr = yr if yr is not None else (years[-1] if years else None)
        d = (stmts.get(yr) or stmts.get(str(yr)) or {}).get(section, {})
        return d.get(key) if isinstance(d, dict) else None

    def kv_row(x, yy, w, label, value, vcol=INK, lf=7):
        c.setFillColor(MUTE); c.setFont("Helvetica", lf); c.drawString(x, yy, label)
        c.setFillColor(vcol); c.setFont("Helvetica-Bold", lf + 0.5)
        c.drawRightString(x + w, yy, str(value)[:26])

    # LEFT — financial highlights (5-yr P&L)
    yL = head(yL, "Financial Highlights  ·  Rs Cr", x=colL, x2=colL + halfW)
    rowsdef = ([("Interest Inc.", "interest_income", "revenue"), ("NII", "nii", None),
                ("Provisions", "provisions", None), ("PAT", "pat", None)] if is_fin else
               [("Revenue", "revenue", None), ("EBITDA", "ebitda", None),
                ("EBIT", "ebit", None), ("PBT", "pbt", None), ("PAT", "pat", None)])
    yc = halfW / (len(years) + 1.4)
    c.setFont("Helvetica", 6); c.setFillColor(MUTE)
    for j, yr in enumerate(reversed(years)):
        c.drawRightString(colL + halfW - j * yc, yL, f"FY{str(yr)[-2:]}")
    yL -= 10
    for lbl, k1, k2 in rowsdef:
        bold = lbl == "PAT"
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 7.3)
        c.setFillColor(INK if bold else MUTE); c.drawString(colL, yL, lbl)
        for j, yr in enumerate(reversed(years)):
            pl = (stmts.get(yr) or stmts.get(str(yr)) or {}).get("PL", {})
            val = pl.get(k1) or (pl.get(k2) if k2 else None)
            c.setFillColor(HEAD if (bold and j == 0) else INK)
            c.setFont("Helvetica-Bold" if bold else "Helvetica", 7.3)
            c.drawRightString(colL + halfW - j * yc, yL, _fc(val))
        yL -= 10.5
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
        x = colL + col * gw; yy = yL - row * 12.5
        try:
            disp = fmt(float(val)) if val is not None else "—"
        except Exception:
            disp = "—"
        c.setFillColor(MUTE); c.setFont("Helvetica", 6.5); c.drawString(x, yy, lb)
        c.setFillColor(INK); c.setFont("Helvetica-Bold", 8); c.drawString(x + gw - 42, yy, disp)
    yL -= ((len(ratios) + 1) // 2) * 12.5 + 6

    # LEFT — balance sheet & cash flow (latest FY snapshot)
    yL = head(yL, "Balance Sheet & Cash Flow  ·  latest FY", x=colL, x2=colL + halfW)
    bscf = ([("Net worth", sec("BS", "net_worth") or sec("BS", "equity")),
             ("Total assets", sec("BS", "total_assets")),
             ("Borrowings", sec("BS", "borrowings") or sec("BS", "total_debt")),
             ("Cash & equiv.", sec("BS", "cash"))] if is_fin else
            [("Net worth", sec("BS", "net_worth") or sec("BS", "equity")),
             ("Total debt", sec("BS", "total_debt") or sec("BS", "borrowings")),
             ("Operating CF", sec("CF", "operating_cf")),
             ("Free cash flow", sec("CF", "fcf")),
             ("Capex", sec("CF", "capex"))])
    for lb, val in bscf:
        kv_row(colL, yL, halfW, lb, _fc(val) if val is not None else "—")
        yL -= 11.5

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
            yR -= 12.5
        yR -= 5

    # RIGHT — valuation + the DCF drivers behind it
    yR = head(yR, "Valuation & DCF Drivers", x=colR, x2=colR + halfW)
    _p = lambda x, d=1: f"{x*100:.{d}f}%" if isinstance(x, (int, float)) else "—"
    if is_fin:
        drivers = [("Method", (rec or {}).get("primary_method") or v.get("method") or "Residual Income"),
                   ("Forecast ROE", _p(a.get("forecast_roe"))),
                   ("Terminal ROE", _p(a.get("terminal_roe"))),
                   ("Cost of equity Ke", _p(v.get("ke"))),
                   ("Terminal growth", _p(a.get("terminal_growth")))]
    else:
        drivers = [("Method", (rec or {}).get("primary_method") or v.get("method") or "FCFF DCF"),
                   ("Rev. growth (stg-1)", _p(a.get("rev_growth"))),
                   ("EBIT margin", _p(a.get("ebit_margin"))),
                   ("WACC", _p(v.get("wacc"))),
                   ("Terminal growth", _p(a.get("terminal_growth"))),
                   ("Forecast horizon", f"{int(a['fade_years'])} yrs" if a.get("fade_years") else "—")]
    for lb, val in drivers:
        kv_row(colR, yR, halfW, lb, val)
        yR -= 11.5
    yR -= 3

    # RIGHT — the valuation bridge (PV → EV → equity → per share)
    bridge = [("PV explicit", v.get("pv_explicit")), ("PV terminal", v.get("tv_pv")),
              ("Enterprise value", v.get("ev")), ("Equity value", v.get("equity_val"))]
    bridge = [(lb, vv) for lb, vv in bridge if vv is not None]
    if bridge:
        yR -= 2
        for lb, vv in bridge:
            kv_row(colR, yR, halfW, lb, f"Rs {_fc(vv)} Cr")
            yR -= 11
    # RIGHT — valuation triangulation (the methods behind the blended fair value;
    # the Dividend Discount cross-check shows for any payer, weighted only for
    # stable high payers).
    comps = [cm for cm in ((rec or {}).get("components") or [])
             if (cm.get("capped") if cm.get("capped") is not None else cm.get("value"))]
    if len(comps) > 1:
        yR -= 2
        for cm in comps:
            cv = cm.get("capped") if cm.get("capped") is not None else cm.get("value")
            wt = cm.get("weight") or 0
            kv_row(colR, yR, halfW, f"{cm.get('method')} ({wt*100:.0f}%)",
                   f"Rs {cv:,.0f}" if cv else "—")
            yR -= 11
        yR -= 2

    kv_row(colR, yR, halfW, "Fair value / share", f"Rs {intrinsic:,.0f}" if intrinsic else "—", vcol=HEAD, lf=7.5)
    yR -= 11.5
    kv_row(colR, yR, halfW, "Margin of safety", f"{mos:+.1f}%" if mos is not None else "—",
           vcol=GREEN if (mos or 0) > 0 else RED, lf=7.5)
    yR -= 13

    # RIGHT — analyst consensus (the street cross-check)
    if analyst and (analyst.get("target") or analyst.get("rating")):
        yR = head(yR, "Analyst Consensus", x=colR, x2=colR + halfW)
        up = analyst.get("upside")
        arows = [("Target", f"Rs {analyst['target']:,.0f}" if analyst.get("target") else "—"),
                 ("Rating", (analyst.get("rating") or "—")),
                 ("Implied upside", f"{up*100:+.1f}%" if up is not None else "—"),
                 ("# Analysts", str(analyst.get("n")) if analyst.get("n") else "—")]
        for lb, val in arows:
            vcol = GREEN if (lb == "Implied upside" and (up or 0) > 0) else RED if (lb == "Implied upside" and up is not None) else INK
            kv_row(colR, yR, halfW, lb, val, vcol=vcol)
            yR -= 11.5

    # ── Peer comparison (full width) ─────────────────────────────────────────
    y = min(yL, yR) - 10
    if peers:
        y = head(y, f"Peer Comparison  ·  {(rec or {}).get('valuation_sector') or co.sector}")
        cols = [("Ticker", 0.0, "l"), ("P/E", 0.42, "r"), ("P/B", 0.56, "r"),
                ("ROE", 0.70, "r"), ("MoS", 0.84, "r"), ("Verdict", 1.0, "r")]
        tw = W - 2 * M
        c.setFont("Helvetica", 6.2); c.setFillColor(MUTE)
        for name, frac, al in cols:
            xx = M + frac * tw
            (c.drawRightString if al == "r" else c.drawString)(xx, y, name.upper())
        y -= 3
        c.setStrokeColor(LINE); c.setLineWidth(0.4); c.line(M, y, W - M, y); y -= 9
        # own row first (highlighted)
        def peer_row(tk, pe, pb, roe, ms, vd, own=False):
            c.setFont("Helvetica-Bold" if own else "Helvetica", 7.2)
            c.setFillColor(HEAD if own else INK)
            c.drawString(M, y0[0], tk[:14])
            c.setFont("Helvetica", 7.2); c.setFillColor(INK)
            c.drawRightString(M + 0.42 * tw, y0[0], f"{pe:.1f}x" if isinstance(pe, (int, float)) and pe else "—")
            c.drawRightString(M + 0.56 * tw, y0[0], f"{pb:.2f}x" if isinstance(pb, (int, float)) and pb else "—")
            c.drawRightString(M + 0.70 * tw, y0[0], f"{roe*100:.1f}%" if isinstance(roe, (int, float)) and roe else "—")
            c.setFillColor(GREEN if (isinstance(ms, (int, float)) and ms > 0) else RED if isinstance(ms, (int, float)) else INK)
            c.drawRightString(M + 0.84 * tw, y0[0], f"{ms*100:+.0f}%" if isinstance(ms, (int, float)) else "—")
            c.setFillColor(_verdict_color(vd)); c.setFont("Helvetica-Bold", 6.8)
            c.drawRightString(M + tw, y0[0], (vd or "—")[:10])
        y0 = [y]
        peer_row(co.ticker, get_m("pe_ratio"), get_m("pb_ratio"), get_m("roe"),
                 (mos / 100 if mos is not None else None), verdict, own=True)
        y0[0] -= 11
        for p in peers[:5]:
            peer_row(p.get("ticker") or "—", p.get("pe"), p.get("pb"), p.get("roe"),
                     p.get("mos"), p.get("verdict"))
            y0[0] -= 11
        y = y0[0] - 4

    # ── Thesis (full width) ──────────────────────────────────────────────────
    y = head(y - 2, "Investment Thesis")
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

    # ── What to watch (forward monitoring triggers — distinct from the risks
    #    already listed in the thesis) ──────────────────────────────────────
    watch = []
    up = (analyst or {}).get("upside")
    if mos is not None and up is not None and intrinsic:
        watch.append(f"Model vs street: our fair value Rs {intrinsic:,.0f} ({mos:+.0f}% MoS) vs the "
                     f"consensus target Rs {(analyst or {}).get('target', 0):,.0f} ({up*100:+.0f}%) — "
                     f"watch which the next two prints validate.")
    if mos is not None and mos > 25:
        watch.append("The wide model-vs-price gap rests on the forecast growth persisting; a "
                     "deceleration would compress the multiple quickly, so track the growth trajectory.")
    watch.append("Quarterly results vs the model's revenue/margin path, management guidance on the "
                 "concall, and any change in promoter holding or pledge.")
    if y > 120 and watch:
        y = head(y - 4, "What to Watch")
        for item in watch[:4]:
            for k, ln in enumerate(wrap_at(item, 150)):
                c.setFillColor(AMBER); c.setFont("Helvetica-Bold", 8)
                if k == 0:
                    c.drawString(M, y, "•")
                c.setFillColor(INK); c.setFont("Helvetica", 8)
                c.drawString(M + 10, y, ln)
                y -= 10.5
            y -= 1.5

    # ── Footer (page 1) ───────────────────────────────────────────────────────
    def footer(page):
        c.setStrokeColor(LINE); c.setLineWidth(0.5); c.line(M, 26, W - M, 26)
        c.setFillColor(FAINT); c.setFont("Helvetica", 6.3)
        c.drawString(M, 18, "Equity Terminal — independent, evidence-based research. Educational use only; not SEBI-registered investment advice.")
        c.drawRightString(W - M, 18, f"{datetime.today().strftime('%d %b %Y')}  ·  {page}")
    footer("1 / 2")

    # ══ PAGE 2 — latest quarter, management commentary, explicit forecast ══════
    rows_dcf = v.get("rows") or []
    c.showPage()
    c.setFillColor(PAPER); c.rect(0, 0, W, H, fill=1, stroke=0)
    c.setFillColor(HEAD); c.rect(0, H - 4, W, 4, fill=1, stroke=0)
    y = H - M
    c.setFillColor(INK); c.setFont("Helvetica-Bold", 16)
    c.drawString(M, y - 15, f"{co.name[:50]}")
    c.setFillColor(MUTE); c.setFont("Helvetica", 8.5)
    c.drawString(M, y - 28, f"{co.ticker}   ·   Results, Management Commentary & Forecast")
    y -= 46

    # ── Latest reported quarter ───────────────────────────────────────────────
    if quarter and (quarter.get("sales") is not None or quarter.get("pat") is not None):
        y = head(y, f"Latest Reported Quarter  ·  {quarter.get('quarter') or ''}")
        def _yoy(x):
            return (f"{x*100:+.0f}% YoY" if isinstance(x, (int, float)) else "")
        qcells = [("Revenue", f"Rs {_fc(quarter.get('sales'))} Cr", _yoy(quarter.get("sales_yoy"))),
                  ("PAT", f"Rs {_fc(quarter.get('pat'))} Cr", _yoy(quarter.get("pat_yoy"))),
                  ("Op. margin", f"{quarter.get('opm'):.0f}%" if quarter.get("opm") is not None else "—", ""),
                  ("EPS", f"Rs {quarter.get('eps'):.1f}" if quarter.get("eps") is not None else "—", "")]
        qcw = (W - 2 * M) / len(qcells)
        c.setFillColor(PANEL); c.roundRect(M, y - 34, W - 2 * M, 34, 4, fill=1, stroke=0)
        for i, (lb, val, yoy) in enumerate(qcells):
            x = M + i * qcw + 12
            c.setFillColor(MUTE); c.setFont("Helvetica", 6.4); c.drawString(x, y - 11, lb.upper())
            c.setFillColor(INK); c.setFont("Helvetica-Bold", 12); c.drawString(x, y - 25, val)
            if yoy:
                c.setFillColor(GREEN if yoy.startswith("+") else RED)
                c.setFont("Helvetica-Bold", 7.5); c.drawRightString(M + (i + 1) * qcw - 8, y - 25, yoy)
        y -= 44
        if cagr:
            def _cg(block, key):
                for kk, vv in (cagr.get(block) or {}).items():
                    if key in kk and vv and vv != "%":
                        return vv
                return None
            bits = []
            s3, s5 = _cg("Compounded Sales Growth", "3 Year"), _cg("Compounded Sales Growth", "5 Year")
            p3, p5 = _cg("Compounded Profit Growth", "3 Year"), _cg("Compounded Profit Growth", "5 Year")
            if s3 or s5: bits.append(f"Sales CAGR {s3 or '—'} (3y) · {s5 or '—'} (5y)")
            if p3 or p5: bits.append(f"Profit CAGR {p3 or '—'} (3y) · {p5 or '—'} (5y)")
            if bits:
                c.setFillColor(MUTE); c.setFont("Helvetica", 8)
                c.drawString(M, y, "Track record — " + "     ".join(bits)); y -= 16

    # ── Management commentary from the earnings call ──────────────────────────
    pts = (concall or {}).get("points") or {}
    if concall and concall.get("available") is not False and any(pts.get(k) for k in
            ("guidance", "demand", "margins", "capex", "risks")):
        tone = concall.get("tone_score") or 0
        tone_txt = "confident" if tone >= 0.15 else "cautious" if tone <= -0.15 else "balanced"
        y = head(y, f"Management Commentary  ·  concall {concall.get('quarter') or ''}  ·  {tone_txt} tone")
        for lbl, key in (("Guidance", "guidance"), ("Demand", "demand"), ("Margins", "margins"),
                         ("Capex / investment", "capex"), ("Risks flagged", "risks")):
            items = [it for it in (pts.get(key) or []) if it][:2]
            if not items:
                continue
            c.setFillColor(HEAD); c.setFont("Helvetica-Bold", 7.6); c.drawString(M, y, lbl.upper()); y -= 11
            for it in items:
                for k, ln in enumerate(wrap_at(it.strip(), 148)):
                    if k == 0:
                        c.setFillColor(HEAD); c.setFont("Helvetica-Bold", 8); c.drawString(M + 8, y, "·")
                    c.setFillColor(INK); c.setFont("Helvetica", 7.7); c.drawString(M + 16, y, ln)
                    y -= 9.8
                y -= 1.5
            y -= 2
    else:
        y = head(y, "Management Commentary")
        c.setFillColor(MUTE); c.setFont("Helvetica", 8)
        c.drawString(M, y, "No processed earnings-call transcript for this name yet — the concall ingester "
                           "runs daily and back-fills the latest call's guidance, margins and risks.")
        y -= 16

    # ── Explicit forecast schedule + terminal value ───────────────────────────
    if rows_dcf:
        N = len(rows_dcf)
        N1 = max(1, N // 2)
        g_t = a.get("terminal_growth")
        g1 = a.get("rev_growth")
        y = head(y, "Explicit Forecast & Terminal Value  ·  Rs Cr")
        if is_fin:
            cols = [("FY", 0.0, "l"), ("ROE", 0.30, "r"), ("Book value/sh", 0.52, "r"),
                    ("Residual income", 0.78, "r"), ("PV", 1.0, "r")]
        else:
            cols = [("FY", 0.0, "l"), ("Revenue", 0.28, "r"), ("Growth", 0.44, "r"),
                    ("FCFF", 0.72, "r"), ("PV of FCFF", 1.0, "r")]
        tw = W - 2 * M
        c.setFont("Helvetica", 6.4); c.setFillColor(MUTE)
        for name, frac, al in cols:
            (c.drawRightString if al == "r" else c.drawString)(M + frac * tw, y, name.upper())
        y -= 3; c.setStrokeColor(LINE); c.setLineWidth(0.4); c.line(M, y, W - M, y); y -= 10
        prev_rev = None
        for i, r in enumerate(rows_dcf):
            c.setFont("Helvetica", 7.4); c.setFillColor(INK)
            c.drawString(M, y, f"Year {i + 1}")
            if is_fin:
                c.drawRightString(M + 0.30 * tw, y, f"{(r.get('roe') or 0)*100:.1f}%")
                c.drawRightString(M + 0.52 * tw, y, _fc(r.get("bv_begin")))
                c.drawRightString(M + 0.78 * tw, y, _fc(r.get("ri")))
                c.drawRightString(M + tw, y, _fc(r.get("pv")))
            else:
                rev = r.get("rev")
                g = g1 if i == 0 else ((rev / prev_rev - 1) if (rev and prev_rev) else None)
                c.drawRightString(M + 0.28 * tw, y, _fc(rev))
                c.setFillColor(MUTE); c.drawRightString(M + 0.44 * tw, y,
                    f"{g*100:.1f}%" if isinstance(g, (int, float)) else "—")
                c.setFillColor(INK)
                c.drawRightString(M + 0.72 * tw, y, _fc(r.get("fcff")))
                c.drawRightString(M + tw, y, _fc(r.get("pv")))
                prev_rev = rev
            y -= 10.5
        y -= 4
        note = (f"Two-stage fade: the near-term rate (~{g1*100:.0f}%) holds for ~{N1} year(s), then glides "
                f"linearly to the {g_t*100:.1f}% terminal (perpetuity) rate by year {N}. A Gordon-growth "
                f"terminal value at {g_t*100:.1f}% is applied FROM year {N} onward and discounted back — it "
                f"is the PV of every cash flow beyond the explicit window."
                if not is_fin and g1 is not None and g_t is not None else
                (f"ROE fades from the franchise level to the {(g_t or 0.05)*100:.1f}%-growth terminal by year "
                 f"{N}; the residual-income perpetuity is applied from year {N} onward." if is_fin else ""))
        if note:
            c.setFillColor(MUTE); c.setFont("Helvetica", 7.4)
            for ln in wrap_at(note, 150):
                c.drawString(M, y, ln); y -= 9.5

    footer("2 / 2")
    c.showPage(); c.save(); buf.seek(0)
    return buf.read()
