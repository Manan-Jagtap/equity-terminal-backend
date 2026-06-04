"""render.py — institutional single-page NBFC tear-sheet renderer.

Layout (top to bottom):
  masthead  ->  thesis + rating tile  ->  6 KPI cards  ->  full-width
  quarterly+annual progression  ->  two balanced columns of working blocks
  ->  bottom strip (shareholding | operational/peer | valuation tile)  ->  footer.

The fitting loop renders, counts pages, and ratchets --density down (and, only
if needed, drops the lowest-priority blocks) until everything sits on one A4.
"""
from __future__ import annotations
import os
import sys
import ctypes
import ctypes.util
from pathlib import Path

from jinja2 import Environment, BaseLoader, select_autoescape

from .render_css import build_css


# --------------------------------------------------------------------------
# macOS native-lib shim: `railway run` strips DYLD_* from the subprocess env,
# so WeasyPrint can't find pango/gdk-pixbuf. We set the fallback path and
# pre-load the dylibs by absolute path before WeasyPrint imports cairocffi.
# No-op on Linux / inside the sandbox.
# --------------------------------------------------------------------------
def _ensure_native_libs() -> None:
    if sys.platform != "darwin":
        return
    candidates = [
        "/opt/homebrew/lib", "/usr/local/lib",
        "/opt/homebrew/opt/glib/lib", "/opt/homebrew/opt/pango/lib",
        "/opt/homebrew/opt/gdk-pixbuf/lib", "/opt/homebrew/opt/cairo/lib",
        "/opt/homebrew/opt/fontconfig/lib", "/opt/homebrew/opt/harfbuzz/lib",
        "/opt/homebrew/opt/libffi/lib",
    ]
    have = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    parts = [p for p in candidates if os.path.isdir(p)]
    os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join(
        [p for p in (have.split(":") + parts) if p]
    )
    for name in ("gobject-2.0", "pango-1.0", "pangocairo-1.0",
                 "harfbuzz", "fontconfig", "gdk_pixbuf-2.0", "cairo"):
        path = ctypes.util.find_library(name)
        if path:
            try:
                ctypes.CDLL(path, mode=ctypes.RTLD_GLOBAL)
            except OSError:
                pass


# --------------------------------------------------------------------------
# Jinja template. Macros are defined BEFORE use (Jinja has no hoisting).
# --------------------------------------------------------------------------
_TMPL = r"""
{%- macro tone_cls(t) -%}
{%- if t == 'pos' -%}pos{%- elif t == 'neg' -%}neg{%- elif t == 'warn' -%}warn{%- else -%}flat{%- endif -%}
{%- endmacro -%}

{%- macro sechead(title, unit) -%}
<div class="sec"><h2>{{ title }}</h2>{% if unit %}<span class="unit">{{ unit }}</span>{% endif %}</div>
{%- endmacro -%}

{%- macro signcell(v) -%}
{%- set s = (v|string) -%}
{%- if s.startswith('+') -%}<td class="num pos">{{ s }}</td>
{%- elif s.startswith('-') or s.startswith('(') -%}<td class="num neg">{{ s }}</td>
{%- else -%}<td class="num">{{ s }}</td>{%- endif -%}
{%- endmacro -%}

{%- macro gentable(t) -%}
{%- set ncol = t.columns|length -%}
{%- set lblw = t.label_w if t.label_w else 26 -%}
{%- set numw = ((100 - lblw) / ncol) -%}
<table>
<colgroup><col style="width:{{ lblw }}%">{% for c in t.columns %}<col style="width:{{ numw }}%">{% endfor %}</colgroup>
{%- if t.group_spans %}
<thead><tr class="grphead"><th></th>
{%- for g in t.group_spans %}<th colspan="{{ g[1] }}" class="{{ 'gap' if g[0]=='' else '' }}">{{ g[0] }}</th>{% endfor %}
</tr>
<tr><th class="lbl">{{ t.row_header }}</th>{% for c in t.columns %}<th class="num">{{ c }}</th>{% endfor %}</tr></thead>
{%- else %}
<thead><tr><th class="lbl">{{ t.row_header }}</th>{% for c in t.columns %}<th class="num">{{ c }}</th>{% endfor %}</tr></thead>
{%- endif %}
<tbody>
{%- for r in t.rows %}
<tr class="{{ 'tot' if r.total else ('strong' if r.bold else '') }}">
<td class="lbl">{{ r.label }}</td>
{%- for i in range(ncol) %}
{%- set cell = r.cells[i] if i < r.cells|length else '' -%}
<td class="num">{{ cell }}</td>
{%- endfor %}
</tr>
{%- endfor %}
</tbody>
</table>
{%- endmacro -%}

{%- macro metricrows(items) -%}
{%- for m in items %}
<div class="mrow"><span class="ml">{{ m.label }}</span><span class="mv">{{ m.value }}</span>
{%- if m.delta %}<span class="md {{ tone_cls(m.tone) }}">{{ m.delta }}</span>{% else %}<span class="md"></span>{% endif %}</div>
{%- endfor %}
{%- endmacro -%}

<!doctype html><html><head><meta charset="utf-8"><style>{{ css }}</style></head>
<body><div class="page">
<div class="brandedge"></div>

<!-- masthead -->
<div class="mast">
  <div class="mast-l">
    <div class="eyebrow">{{ c.subtype_label }}{% if c.tagline %} &middot; {{ c.tagline }}{% endif %}</div>
    <div class="coname">{{ c.name }}</div>
    <div class="subid">{{ c.ticker }} &middot; {{ c.exchange }} &middot; {{ c.sector }}{% if c.sbr_layer %} &middot; {{ c.sbr_layer }}{% endif %}</div>
  </div>
  <div class="mast-r">
    <div class="qtag">{{ d.quarter_label }}</div>
    <div class="qdates">Reported {{ c.reported_date }}<br>As of {{ d.as_of }}</div>
  </div>
</div>

<!-- thesis + rating -->
<div class="band">
  <div class="thesis"><p>{{ d.thesis_strap }}</p></div>
  {%- if d.rating %}
  <div class="rtile">
    <div class="rl">Street View</div>
    <div class="reco">{{ d.rating.reco }}</div>
    <div class="rrow"><span>Target</span><span>{{ d.rating.tp }}</span></div>
    <div class="rrow"><span>Implied</span><span>{{ d.rating.upside }}</span></div>
  </div>
  {%- endif %}
</div>

<!-- KPI strip -->
<table class="kpis"><colgroup>{% for k in d.headline_kpis %}<col style="width:{{ (100 / (d.headline_kpis|length))|round(3) }}%">{% endfor %}</colgroup>
<tr>
{%- for k in d.headline_kpis %}
  <td class="kpi {{ 'lead' if loop.first else '' }}">
    <span class="kl">{{ k.label }}</span>
    <span class="kv">{{ k.value }}{% if k.unit %}<span class="ku">{{ k.unit }}</span>{% endif %}</span>
    <div class="kd">
      {%- if k.yoy %}<span class="dpair"><span class="{{ tone_cls(k.yoy_tone) }}">{{ k.yoy }}</span><span class="lab">YoY</span></span> {% endif %}
      {%- if k.qoq %}<span class="dpair"><span class="{{ tone_cls(k.qoq_tone) }}">{{ k.qoq }}</span><span class="lab">QoQ</span></span>{% endif %}
    </div>
    {%- if k.sub %}<span class="ksub">{{ k.sub }}</span>{% endif %}
  </td>
{%- endfor %}
</tr></table>

<!-- progression -->
{%- if d.progression %}
<div class="blk" style="margin-top:1.8mm;margin-bottom:0">
  {{ sechead('Quarterly & Annual Progression', d.progression.unit) }}
  {{ gentable(d.progression) }}
</div>
{%- endif %}

<!-- two-column working region -->
<table class="cols"><colgroup><col style="width:50%"><col style="width:50%"></colgroup><tr>
  <td class="col l">
    {%- if d.asset_quality %}
    <div class="blk">
      {{ sechead('Asset Quality & Provisioning', d.asset_quality.unit or 'Ind-AS ECL') }}
      {%- if d.asset_quality.stages %}
      <table>
        <colgroup><col style="width:40%"><col style="width:20%"><col style="width:20%"><col style="width:20%"></colgroup>
        <thead><tr><th class="lbl">Stage</th><th class="num">Gross</th><th class="num">ECL</th><th class="num">Cover %</th></tr></thead>
        <tbody>
        {%- for s in d.asset_quality.stages %}
        <tr class="{{ 'tot' if s.total else '' }}"><td class="lbl">{{ s.stage }}</td>
        <td class="num">{{ s.gross }}</td><td class="num">{{ s.ecl }}</td><td class="num">{{ s.coverage }}</td></tr>
        {%- endfor %}
        </tbody>
      </table>
      {%- endif %}
      {%- if d.asset_quality.metrics %}<div style="margin-top:0.6mm">{{ metricrows(d.asset_quality.metrics) }}</div>{% endif %}
    </div>
    {%- endif %}

    {%- if d.ratios %}
    <div class="blk">
      {{ sechead('Profitability, Spreads & Capital', d.ratios_unit or 'FY26 / Q4') }}
      {{ metricrows(d.ratios) }}
    </div>
    {%- endif %}

    {%- if 'commentary' not in dropped and d.commentary %}
    <div class="blk">
      {{ sechead('Management Commentary & Guidance', '') }}
      {%- for cm in d.commentary %}
      <div class="cmt">
        <div class="ct">{{ cm.theme }}</div>
        <div class="cq">&ldquo;{{ cm.quote }}&rdquo;</div>
        <div class="cby"><b>{{ cm.speaker }}</b>{% if cm.title %} &middot; {{ cm.title }}{% endif %}</div>
      </div>
      {%- endfor %}
      {%- if 'guidance' not in dropped and d.guidance %}
      <table class="gchips">
      {%- for g in d.guidance %}{% if loop.index0 % 2 == 0 %}<tr>{% endif %}<td class="gc"><div class="gcin"><span class="gl">{{ g.label }}</span><span class="gv">{{ g.value }}</span></div></td>{% if loop.index0 % 2 == 1 or loop.last %}</tr>{% endif %}{% endfor %}
      </table>
      {%- endif %}
    </div>
    {%- endif %}
  </td>
  <td class="col r">
    {%- if 'sector' not in dropped and d.sector_block %}
    <div class="blk">
      {{ sechead(d.sector_block.title, d.sector_block.unit or '') }}
      {%- if d.sector_block.trend %}
      <table class="trend"><tr>
      {%- for t in d.sector_block.trend %}
        <td class="tcell"><div class="tp">{{ t.period }}</div><div class="tv">{{ t.value }}</div>{% if t.sub %}<div class="ts">{{ t.sub }}</div>{% endif %}</td>
      {%- endfor %}
      </tr></table>
      {%- endif %}
      {%- if d.sector_block.stats %}
      <table class="statgrid">
      {%- for s in d.sector_block.stats %}{% if loop.index0 % 2 == 0 %}<tr>{% endif %}
        <td class="stat"><div class="sv">{{ s.value }}</div><div class="sl">{{ s.label }}</div>{% if s.sub %}<div class="ss">{{ s.sub }}</div>{% endif %}</td>
      {%- if loop.index0 % 2 == 1 or loop.last %}</tr>{% endif %}{% endfor %}
      </table>
      {%- endif %}
      {%- if d.sector_block.rows %}{{ gentable(d.sector_block) }}{% endif %}
    </div>
    {%- endif %}

    {%- if 'borrowing' not in dropped and d.borrowing_mix %}
    <div class="blk">
      {{ sechead('Borrowing & Funding Mix', d.borrowing_mix.total) }}
      <div class="bmix">
      {%- for b in d.borrowing_mix.rows %}
        <div class="brow">
          <span class="bl">{{ b.label }}</span>
          <span class="btrack"><span class="bfill" style="width:{{ b.pct_num }}%"></span></span>
          <span class="bamt">{{ b.amount }}</span><span class="bpct">{{ b.pct }}</span>
        </div>
      {%- endfor %}
      </div>
      {%- if d.borrowing_mix.note %}<div class="valnote">{{ d.borrowing_mix.note }}</div>{% endif %}
    </div>
    {%- endif %}

    {%- if 'estimates' not in dropped and d.forward_estimates %}
    <div class="blk">
      {{ sechead(d.forward_estimates.title or 'Multi-Year Financials', d.forward_estimates.unit) }}
      {{ gentable(d.forward_estimates) }}
    </div>
    {%- endif %}

    {%- if 'dupont' not in dropped and d.dupont %}
    <div class="blk">
      {{ sechead(d.dupont.title or 'RoA Bridge (DuPont)', d.dupont.unit or '% of avg assets') }}
      {{ metricrows(d.dupont.rows) }}
    </div>
    {%- endif %}
  </td>
</tr></table>

<!-- bottom strip -->
<table class="foot-strip"><tr>
  {%- if d.shareholding %}
  <td class="fcol">
    {{ sechead('Shareholding', d.shareholding.unit or '') }}
    {{ gentable(d.shareholding) }}
  </td>
  {%- endif %}
  {%- if 'segments' not in dropped and d.segments %}
  <td class="fcol">
    {{ sechead(d.segments.title, d.segments.unit or '') }}
    {{ gentable(d.segments) }}
  </td>
  {%- endif %}
  {%- if d.street %}
  <td class="fcol val">
    <div class="valtile">
      <div class="vhead"><h2>Valuation</h2><span class="as">{{ d.street.note_date or d.as_of }}</span></div>
      <div class="valgrid">
        <div class="vg"><span class="vl">CMP</span><span class="vv">{{ d.street.cmp or '\u2014' }}</span></div>
        <div class="vg"><span class="vl">Mkt Cap</span><span class="vv">{{ d.street.mkt_cap or '\u2014' }}</span></div>
        <div class="vg"><span class="vl">Target</span><span class="vv">{{ d.street.tp or '\u2014' }}</span></div>
        <div class="vg"><span class="vl">P / E</span><span class="vv">{{ d.street.pe or '\u2014' }}</span></div>
        <div class="vg"><span class="vl">P / ABV</span><span class="vv">{{ d.street.pabv or d.street.pb or '\u2014' }}</span></div>
        <div class="vg"><span class="vl">View</span><span class="vv">{{ d.street.reco or '\u2014' }}</span></div>
        <div class="vg"><span class="vl">EPS</span><span class="vv">{{ d.street.eps or '\u2014' }}</span></div>
        <div class="vg"><span class="vl">ABVPS</span><span class="vv">{{ d.street.abvps or d.street.bvps or '\u2014' }}</span></div>
        <div class="vg"><span class="vl">52w</span><span class="vv">{{ d.street.range_52w or '\u2014' }}</span></div>
      </div>
      {%- if d.street.note %}<div class="valnote">{{ d.street.note }}</div>{% endif %}
    </div>
  </td>
  {%- endif %}
</tr></table>


<!-- footer -->
<div class="footer">
  <div class="src">
    {%- for s in d.sources %}<b>{{ s.doc_type }}</b> ({{ s.filing_date }}){% if not loop.last %} &middot; {% endif %}{% endfor %}
  </div>
  <div class="disc">Figures as reported; verify against filings. Not investment advice. Generated {{ d.as_of }}. <span style="opacity:0.55">[r6]</span></div>
</div>

</div></body></html>
"""

_env = Environment(loader=BaseLoader(), autoescape=select_autoescape(["html"]))
_template = _env.from_string(_TMPL)


def render_html(data: dict, density: float = 1.0, dropped: set | None = None) -> str:
    dropped = dropped or set()
    css = build_css(data["company"]["accent"], density)
    return _template.render(css=css, c=data["company"], d=data, dropped=dropped)


# fitting ladders: shrink density first, then drop lowest-priority blocks.
# Wider, finer range so it can always reach a single page even on stricter
# WeasyPrint builds that metric fonts slightly larger.
_DENSITY_LADDER = [round(1.00 - 0.0125 * i, 4) for i in range(0, 33)]  # 1.00 -> 0.60
_DROP_LADDER = [
    set(),
    {"dupont"},
    {"dupont", "segments"},
    {"dupont", "segments", "guidance"},
    {"dupont", "segments", "guidance", "commentary"},
]

# A4 printable height in CSS px at 96dpi: 297mm ~= 1122.5px.
# Generous headroom: different WeasyPrint/font builds lay the same content out
# a little taller, so we target well inside the sheet to stay 1-page everywhere.
_SAFETY_PX = 56.0


def _page_div_box(page):
    """Return the innermost .page <div> box (html>body>div)."""
    pb = page._page_box
    box = pb
    for _ in range(6):
        kids = [c for c in (getattr(box, "children", []) or [])
                if type(c).__name__ != "MarginBox"]
        if not kids:
            break
        box = kids[0]
        if getattr(box, "element_tag", None) == "div":
            return box
    return box


def _overflows(doc) -> bool:
    if len(doc.pages) > 1:
        return True
    page = doc.pages[0]
    page_h = page._page_box.height
    div = _page_div_box(page)
    bottom = getattr(div, "position_y", 0.0) + getattr(div, "height", 0.0)
    if bottom > (page_h - _SAFETY_PX):
        return True
    # last-resort: scan the page box tree; if any laid-out box bottom exceeds
    # the sheet, the layout fragmented even though page count read 1.
    try:
        deepest = _scan_bottom(page._page_box)
        if deepest > (page_h - _SAFETY_PX):
            return True
    except Exception:
        pass
    return False


def _scan_bottom(box, limit=1500):
    """Best-effort deepest content bottom, ignoring page-margin boxes."""
    best = 0.0
    stack = [box]
    seen = 0
    while stack and seen < limit:
        b = stack.pop()
        seen += 1
        if type(b).__name__ == "MarginBox":
            continue
        py = getattr(b, "position_y", None)
        h = getattr(b, "height", None)
        if isinstance(py, (int, float)) and isinstance(h, (int, float)):
            tag = getattr(b, "element_tag", None)
            # only count real flow content, not the page/html/body frame
            if tag not in (None, "html", "body"):
                if py + h > best:
                    best = py + h
        for c in getattr(b, "children", []) or []:
            stack.append(c)
    return best


def render_pdf(data: dict, out_path: str | Path, verbose: bool = True) -> dict:
    _ensure_native_libs()
    from weasyprint import HTML  # imported after lib shim

    out_path = Path(out_path)
    best = None  # (doc, density, dropped, pages)

    if verbose:
        # how many font files actually embedded (sanity for the env)
        try:
            from .render_css import _FONT_DIR, _FONT_FACES
            present = sum(1 for *_ , fn in _FONT_FACES if (_FONT_DIR / fn).exists())
            print(f"    [diag] fonts embedded: {present}/{len(_FONT_FACES)} "
                  f"from {_FONT_DIR}")
        except Exception as e:
            print(f"    [diag] font check failed: {e}")

    for dropped in _DROP_LADDER:
        for density in _DENSITY_LADDER:
            html = render_html(data, density=density, dropped=dropped)
            doc = HTML(string=html).render()
            pages = len(doc.pages)
            ok = (pages == 1 and not _overflows(doc))
            if verbose and (density in (1.0, 0.85, 0.7) or ok):
                try:
                    page = doc.pages[0]
                    ph = page._page_box.height
                    sb = _scan_bottom(page._page_box)
                    print(f"    [diag] density={density:.3f} drop={sorted(dropped) or 'none'} "
                          f"pages={pages} content_bottom={sb:.0f}/{ph:.0f}px ok={ok}")
                except Exception:
                    pass
            if ok:
                doc.write_pdf(str(out_path))
                if verbose:
                    print(f"    fit: density={density:.3f} "
                          f"dropped={sorted(dropped) or 'none'} -> 1 page")
                return {"density": density, "dropped": sorted(dropped), "pages": 1}
            best = (doc, density, dropped, pages)
    # fallback: write the last (least-bad, smallest-density) attempt
    if best is not None:
        doc, density, dropped, pages = best
        doc.write_pdf(str(out_path))
        if verbose:
            print(f"    WARN: could not fit to 1 page; wrote {pages}-page "
                  f"at density={density:.3f}")
        return {"density": density, "dropped": sorted(dropped), "pages": pages}
    return {"density": None, "dropped": None, "pages": None}
