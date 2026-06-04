"""render_css.py — institutional tear-sheet design system.

Key principles encoded here:
  * A true baseline grid: every table row is exactly --rh tall, so all
    numbers across all blocks sit on a shared rhythm.
  * Restrained accent: the brand colour appears in exactly three zones
    (left brand edge, the rating tile, and the hairline atop the lead KPI).
    Every heading is ink, never accent.
  * Hairline tables, no zebra: a heavier rule under headers and at table
    feet, whisper-thin rules between data rows.
  * rem-based scale off a density-driven root font-size, so the fitting
    loop genuinely compresses type AND vertical rhythm together.
"""
from __future__ import annotations
import base64
from pathlib import Path

_FONT_DIR = Path(__file__).parent / "fonts"
_FONT_FACES = [
    ("IBM Plex Sans", 400, "normal", "ibm-plex-sans-latin-400-normal.woff2"),
    ("IBM Plex Sans", 500, "normal", "ibm-plex-sans-latin-500-normal.woff2"),
    ("IBM Plex Sans", 600, "normal", "ibm-plex-sans-latin-600-normal.woff2"),
    ("IBM Plex Sans", 700, "normal", "ibm-plex-sans-latin-700-normal.woff2"),
    ("IBM Plex Serif", 400, "normal", "ibm-plex-serif-latin-400-normal.woff2"),
    ("IBM Plex Serif", 400, "italic", "ibm-plex-serif-latin-400-italic.woff2"),
    ("IBM Plex Serif", 600, "normal", "ibm-plex-serif-latin-600-normal.woff2"),
    ("IBM Plex Mono", 400, "normal", "ibm-plex-mono-latin-400-normal.woff2"),
    ("IBM Plex Mono", 500, "normal", "ibm-plex-mono-latin-500-normal.woff2"),
    ("IBM Plex Mono", 600, "normal", "ibm-plex-mono-latin-600-normal.woff2"),
]


def _font_face_css() -> str:
    out = []
    for family, weight, style, fname in _FONT_FACES:
        fp = _FONT_DIR / fname
        if not fp.exists():
            continue
        b64 = base64.b64encode(fp.read_bytes()).decode()
        out.append(
            f"@font-face{{font-family:'{family}';font-weight:{weight};"
            f"font-style:{style};font-display:block;"
            f"src:url(data:font/woff2;base64,{b64}) format('woff2');}}"
        )
    return "\n".join(out)


_CSS = """
{{ font_faces }}
:root{
  --accent: {{ accent }};
  --accent-wash: {{ accent }}14;
  --density: {{ density }};
  --rh: {{ rh_pt }}pt;
  --bg-page:#FFFFFF;
  --bg-tile:#FBFAF7;
  --ink-1:#16130F;
  --ink-2:#46423B;
  --ink-3:#8C877D;
  --ink-4:#B4AFA4;
  --rule-strong:#16130F;
  --rule-mid:#BDB8AD;
  --rule-hair:#E6E2D8;
  --pos:#0A7A33;
  --neg:#BE1430;
  --warn:#9A6B05;
}
*{margin:0;padding:0;box-sizing:border-box}
@page{size:A4;margin:0}
html{font-size:{{ root_pt }}pt}
body{
  background:var(--bg-page);color:var(--ink-1);
  font-family:'IBM Plex Sans',sans-serif;
  -webkit-font-smoothing:antialiased;
  font-size:1rem;line-height:1.18;
  font-variant-numeric:tabular-nums lining-nums;
  font-feature-settings:"tnum" 1,"lnum" 1;
}
.page{position:relative;width:210mm;
  padding:8mm 9mm 6.4mm 9.8mm}
.mono{font-family:'IBM Plex Mono',monospace;font-variant-numeric:tabular-nums lining-nums;
  letter-spacing:-0.01em}
.num{font-family:'IBM Plex Mono',monospace;font-variant-numeric:tabular-nums lining-nums;
  text-align:right;letter-spacing:-0.015em;white-space:nowrap}
.pos{color:var(--pos)} .neg{color:var(--neg)} .warn{color:var(--warn)} .flat{color:var(--ink-3)}
.up::before{content:"\\2191\\2009";font-size:0.82em}
.dn::before{content:"\\2193\\2009";font-size:0.82em}

/* ---- left brand edge : accent zone 1 ---- */
.brandedge{position:absolute;left:0;top:0;bottom:0;width:2.0mm;background:var(--accent)}

/* ---- masthead ---- */
.mast{display:flex;justify-content:space-between;align-items:flex-end;
  border-bottom:1.4pt solid var(--rule-strong);padding-bottom:1.2mm}
.mast-l{min-width:0}
.eyebrow{font-size:0.80rem;font-weight:600;letter-spacing:0.13em;text-transform:uppercase;
  color:var(--accent);margin-bottom:0.4mm}
.coname{font-family:'IBM Plex Serif',serif;font-weight:600;font-size:2.55rem;
  line-height:0.96;letter-spacing:-0.012em;color:var(--ink-1)}
.subid{font-size:0.86rem;color:var(--ink-2);margin-top:0.9mm;letter-spacing:0.01em}
.subid b{color:var(--ink-1);font-weight:600}
.mast-r{text-align:right;flex-shrink:0;padding-left:6mm}
.qtag{font-family:'IBM Plex Serif',serif;font-weight:600;font-size:1.62rem;
  line-height:1;color:var(--ink-1)}
.qdates{font-size:0.78rem;color:var(--ink-3);margin-top:1mm;letter-spacing:0.04em;
  text-transform:uppercase;line-height:1.45}

/* ---- thesis + rating row ---- */
.band{display:flex;gap:4mm;margin-top:1.6mm;align-items:stretch}
.thesis{flex:1;min-width:0;border-left:2pt solid var(--accent);
  padding:0.3mm 0 0.3mm 2.6mm;display:flex;align-items:center}
.thesis p{font-family:'IBM Plex Serif',serif;font-size:1.06rem;line-height:1.28;
  color:var(--ink-1);font-style:italic}
/* rating tile : accent zone 2 */
.rtile{flex-shrink:0;width:46mm;background:var(--accent);color:#fff;
  border-radius:0.6mm;padding:1.4mm 2.6mm 1.6mm}
.rtile .rl{font-size:0.74rem;letter-spacing:0.13em;text-transform:uppercase;opacity:0.82}
.rtile .reco{font-family:'IBM Plex Serif',serif;font-weight:600;font-size:1.5rem;
  line-height:1.02;margin:0.2mm 0 0.8mm}
.rtile .rrow{display:flex;justify-content:space-between;font-size:0.82rem;
  line-height:1.5;border-top:0.5pt solid rgba(255,255,255,0.28);padding-top:0.6mm}
.rtile .rrow span:last-child{font-family:'IBM Plex Mono',monospace;font-weight:500}

/* ---- KPI strip : fixed table (robust across WeasyPrint versions) ---- */
.kpis{width:100%;table-layout:fixed;border-collapse:collapse;margin-top:1.8mm;
  border-top:0.6pt solid var(--rule-mid);border-bottom:0.6pt solid var(--rule-mid)}
.kpis td.kpi{vertical-align:top;padding:1.3mm 2mm 1.4mm;
  border-left:0.5pt solid var(--rule-hair);overflow:hidden}
.kpis td.kpi:first-child{border-left:none}
.kpis td.kpi.lead{border-top:1.4pt solid var(--accent)}
.kpi .kl{font-size:0.78rem;font-weight:600;letter-spacing:0.04em;text-transform:uppercase;
  color:var(--ink-3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display:block}
.kpi .kv{font-family:'IBM Plex Serif',serif;font-weight:600;font-size:1.58rem;
  line-height:1.02;color:var(--ink-1);margin-top:0.6mm;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis;display:block}
.kpi .kv .ku{font-family:'IBM Plex Sans';font-size:0.5em;font-weight:500;
  color:var(--ink-3);margin-left:0.4mm;letter-spacing:0}
.kpi .kd{margin-top:0.7mm;font-size:0.76rem;font-weight:600;
  white-space:normal;line-height:1.32}
.kpi .kd .dpair{white-space:nowrap;margin-right:1.6mm}
.kpi .kd .lab{color:var(--ink-4);font-weight:500;margin-left:0.5mm}
.kpi .ksub{font-size:0.74rem;color:var(--ink-3);margin-top:0.5mm;line-height:1.2;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display:block}

/* ---- section headers (ink, never accent) ---- */
.sec{display:flex;align-items:baseline;justify-content:space-between;
  border-bottom:0.9pt solid var(--rule-strong);
  padding-bottom:0.5mm;margin-bottom:0.6mm}
.sec h2{font-size:0.86rem;font-weight:700;letter-spacing:0.085em;text-transform:uppercase;
  color:var(--ink-1)}
.sec .unit{font-size:0.74rem;color:var(--ink-3);letter-spacing:0.02em;font-weight:500;
  white-space:nowrap}

/* ---- generic data table : the baseline grid ---- */
table{width:100%;border-collapse:collapse;table-layout:fixed}
.blk table, .kpis, .foot-strip, .trend, .statgrid, .gchips{
  page-break-inside:avoid;break-inside:avoid}
tr{page-break-inside:avoid;break-inside:avoid}
th,td{height:var(--rh);line-height:var(--rh);padding:0 0.7mm;
  font-size:0.92rem;vertical-align:middle;overflow:hidden;white-space:nowrap}
thead th{font-size:0.74rem;font-weight:600;letter-spacing:0.03em;text-transform:uppercase;
  color:var(--ink-3);border-bottom:0.7pt solid var(--rule-strong)}
tbody td{border-bottom:0.4pt solid var(--rule-hair);color:var(--ink-2)}
tbody tr:last-child td{border-bottom:none}
td.lbl,th.lbl{text-align:left;color:var(--ink-1);font-weight:500;
  text-overflow:ellipsis}
td.num,th.num{font-family:'IBM Plex Mono',monospace;text-align:right;
  letter-spacing:-0.015em}
th.num{color:var(--ink-3)}
tr.tot td{border-top:0.7pt solid var(--rule-mid);font-weight:700;color:var(--ink-1)}
tr.tot td.num{color:var(--ink-1)}
tr.strong td{font-weight:700;color:var(--ink-1)}
.grphead th{font-size:0.72rem;font-weight:600;letter-spacing:0.07em;text-transform:uppercase;
  color:var(--ink-1);text-align:center;border-bottom:0.4pt solid var(--rule-mid);
  height:0.98rem;line-height:0.98rem}
.grphead th.gap{border-bottom:none}

/* ---- two-column working region : fixed table ---- */
.cols{width:100%;table-layout:fixed;border-collapse:collapse;margin-top:1.6mm;
  page-break-inside:avoid;break-inside:avoid}
.cols td.col{vertical-align:top}
.cols td.col.l{padding-right:3mm}
.cols td.col.r{padding-left:3mm}
.blk{margin-bottom:2.1mm}
.blk:last-child{margin-bottom:0}

/* ---- ratio / metric rows (label ... value delta) ---- */
.mrow{display:flex;align-items:baseline;height:var(--rh);line-height:var(--rh);
  border-bottom:0.4pt solid var(--rule-hair)}
.mrow:last-child{border-bottom:none}
.mrow .ml{flex:1;color:var(--ink-1);font-weight:500;font-size:0.92rem;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mrow .mv{font-family:'IBM Plex Mono',monospace;font-weight:600;font-size:0.96rem;
  color:var(--ink-1);text-align:right;padding-left:1.4mm}
.mrow .md{font-size:0.80rem;font-weight:600;text-align:right;width:18mm;
  padding-left:1.4mm;white-space:nowrap}

/* ---- sector stat chips ---- */
.trend{width:100%;table-layout:fixed;border-collapse:collapse;
  border:0.5pt solid var(--rule-hair);margin-bottom:1.2mm}
.trend td.tcell{text-align:center;padding:0.8mm 0.4mm;vertical-align:top;
  border-left:0.5pt solid var(--rule-hair)}
.trend td.tcell:first-child{border-left:none}
.trend .tp{font-size:0.74rem;letter-spacing:0.04em;text-transform:uppercase;color:var(--ink-3)}
.trend .tv{font-family:'IBM Plex Serif',serif;font-weight:600;font-size:1.16rem;
  color:var(--ink-1);line-height:1.05;margin-top:0.3mm}
.trend .ts{font-size:0.72rem;color:var(--ink-3);margin-top:0.2mm}
.statgrid{width:100%;table-layout:fixed;border-collapse:collapse}
.statgrid td.stat{width:50%;padding:0.9mm 1mm;vertical-align:top;
  border-top:0.5pt solid var(--rule-hair);border-left:0.5pt solid var(--rule-hair)}
.statgrid td.stat:first-child{border-left:none}
.statgrid .sv{font-family:'IBM Plex Serif',serif;font-weight:600;font-size:1.3rem;
  line-height:1.02;color:var(--ink-1)}
.statgrid .sl{font-size:0.76rem;color:var(--ink-2);margin-top:0.3mm;font-weight:500}
.statgrid .ss{font-size:0.72rem;color:var(--ink-3);margin-top:0.1mm}

/* ---- borrowing mix bars ---- */
.bmix .brow{display:flex;align-items:center;height:var(--rh);line-height:var(--rh);
  gap:1.8mm}
.bmix .bl{width:34%;color:var(--ink-1);font-weight:500;font-size:0.90rem;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bmix .btrack{flex:1;height:1.5mm;background:var(--rule-hair);border-radius:0.4mm;
  overflow:hidden}
.bmix .bfill{height:100%;background:var(--accent);opacity:0.80;border-radius:0.4mm}
.bmix .bamt{width:15mm;text-align:right;font-family:'IBM Plex Mono',monospace;
  font-size:0.86rem;color:var(--ink-2)}
.bmix .bpct{width:9mm;text-align:right;font-family:'IBM Plex Mono',monospace;
  font-weight:600;font-size:0.88rem;color:var(--ink-1)}

/* ---- commentary ---- */
.cmt{margin-bottom:1.3mm}
.cmt:last-child{margin-bottom:0}
.cmt .ct{font-size:0.76rem;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;
  color:var(--accent);margin-bottom:0.3mm}
.cmt .cq{font-family:'IBM Plex Serif',serif;font-style:italic;font-size:0.92rem;
  line-height:1.26;color:var(--ink-1);white-space:normal;overflow-wrap:break-word}
.cmt .cby{font-size:0.76rem;color:var(--ink-3);margin-top:0.3mm}
.cmt .cby b{color:var(--ink-2);font-weight:600;font-style:normal}

/* ---- guidance chips ---- */
.gchips{width:100%;table-layout:fixed;border-collapse:collapse;
  border-top:0.5pt solid var(--rule-hair)}
.gchips td.gc{width:50%;padding:0.7mm 1mm;vertical-align:middle;
  border-bottom:0.4pt solid var(--rule-hair);border-left:0.5pt solid var(--rule-hair)}
.gchips td.gc:first-child{border-left:none}
.gchips .gcin{display:flex;justify-content:space-between;align-items:baseline;gap:1mm}
.gchips .gl{font-size:0.82rem;color:var(--ink-2);overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap}
.gchips .gv{font-family:'IBM Plex Mono',monospace;font-weight:600;font-size:0.86rem;
  color:var(--ink-1);white-space:nowrap}

/* ---- bottom strip (shareholding | peers | valuation) : fixed table ---- */
.foot-strip{width:100%;table-layout:fixed;border-collapse:collapse;margin-top:1.8mm}
.foot-strip td.fcol{vertical-align:top;padding-right:6mm}
.foot-strip td.fcol.last{padding-right:0}
.foot-strip td.fcol.val{width:56mm;padding-right:0}
.valtile{background:var(--bg-tile);border:0.6pt solid var(--rule-mid);
  border-radius:0.6mm;padding:1.1mm 2.2mm 1.3mm}
.valtile .vhead{display:flex;justify-content:space-between;align-items:baseline;
  border-bottom:0.6pt solid var(--rule-mid);padding-bottom:0.5mm;margin-bottom:0.5mm}
.valtile .vhead h2{font-size:0.82rem;font-weight:700;letter-spacing:0.08em;
  text-transform:uppercase;color:var(--ink-1)}
.valtile .vhead .as{font-size:0.72rem;color:var(--ink-3)}
.valgrid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:0 2mm}
.valgrid .vg{padding:0.4mm 0;display:flex;flex-direction:column}
.valgrid .vg .vl{font-size:0.72rem;letter-spacing:0.03em;text-transform:uppercase;
  color:var(--ink-3)}
.valgrid .vg .vv{font-family:'IBM Plex Mono',monospace;font-weight:600;font-size:1.0rem;
  color:var(--ink-1);line-height:1.1}
.valnote{font-size:0.76rem;color:var(--ink-3);line-height:1.24;margin-top:0.6mm;
  border-top:0.4pt solid var(--rule-hair);padding-top:0.5mm;
  white-space:normal;overflow-wrap:break-word}

/* ---- footer ---- */
.footer{margin-top:2.2mm;padding-top:1.2mm;border-top:0.9pt solid var(--rule-strong);
  display:flex;justify-content:space-between;align-items:baseline;gap:4mm}
.footer .src{font-size:0.74rem;color:var(--ink-3);line-height:1.3}
.footer .src b{color:var(--ink-2);font-weight:600}
.footer .disc{font-size:0.70rem;color:var(--ink-4);text-align:right;
  line-height:1.3;flex-shrink:0;max-width:78mm}
"""


def build_css(accent: str, density: float) -> str:
    # Concrete point sizes computed in Python so we never depend on the renderer
    # supporting calc() with CSS custom properties (some WeasyPrint builds don't,
    # which silently kills density scaling). 7.7pt is the base root at density 1.0.
    root_pt = 7.7 * density
    rh_pt = 1.16 * root_pt            # row height == 1.16rem equivalent
    grphead_pt = 0.98 * root_pt       # group-header row height
    css = _CSS.replace("{{ font_faces }}", _font_face_css())
    css = css.replace("{{ accent }}", accent)
    css = css.replace("{{ density }}", f"{density:.4f}")
    css = css.replace("{{ root_pt }}", f"{root_pt:.4f}")
    css = css.replace("{{ rh_pt }}", f"{rh_pt:.4f}")
    css = css.replace("{{ grphead_pt }}", f"{grphead_pt:.4f}")
    return css
