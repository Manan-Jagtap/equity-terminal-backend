"""
app/transcript_ingester.py — proactive earnings-call transcript ingestion.

For every covered name we already hold concall transcript LINKS (from the
IndianAPI insight blob and BSE announcements). This module turns those links
into structured, stored intelligence:

  1. Fetch the transcript PDF and extract its text (reuses transcript_nlp).
  2. A deterministic, FREE rule-based extractor pulls the key pointers a PM
     scans a call for — forward guidance, margin commentary, capex/expansion,
     demand/order-book, and risks/headwinds — plus a management-tone score
     from the balance of confident vs. cautious language.
  3. Result is stored one-row-per-company in TranscriptInsight, deduped by
     transcript URL so a call is processed once.

100% rules-based — no LLM, no paid API. (The `with_llm` params below are kept
only for call-site compatibility and are now no-ops; llm_summary is never set.)

The tone score and a "guidance direction" flag feed the Fund Manager engine;
the pointers surface on the company page. No-fabrication: a call with no
extractable text is skipped, never guessed.

Run daily by the scheduler over a bounded slice of the universe so the whole
book refreshes within a week without hammering the sources.
"""
from __future__ import annotations

import datetime as _dt
import logging
import re

log = logging.getLogger("transcript_ingester")

# ── lexicons ─────────────────────────────────────────────────────────────────
_FORWARD = ("we expect", "we anticipate", "going forward", "outlook", "guidance",
            "we aim", "we target", "we are targeting", "next year", "next quarter",
            "coming quarters", "full year", "fy2", "medium term", "we believe we can",
            "we are confident", "on track to", "we plan to", "we intend to",
            "in the coming", "over the next")
_MARGIN = ("margin", "ebitda", "gross margin", "operating margin", "profitability",
           "cost", "realisation", "realization", "pricing")
_CAPEX = ("capex", "capacity", "expansion", "greenfield", "brownfield", "new plant",
          "new facility", "commissioning", "capital expenditure", "invest", "debottleneck")
_DEMAND = ("demand", "order book", "order intake", "orderbook", "bookings", "pipeline",
           "volume", "footfall", "enquir", "inquiry", "utilisation", "utilization")
_RISK = ("headwind", "challenge", "pressure", "weak", "softness", "soft ", "slowdown",
         "slow down", "decline", "de-growth", "degrowth", "concern", "uncertain",
         "delay", "subdued", "muted", "shortfall", "impact", "disruption")

_POS = ("strong", "robust", "healthy", "confident", "record", "momentum", "improv",
        "accelerat", "outperform", "resilient", "encourag", "double-digit", "all-time high",
        "best-ever", "expand", "gaining", "traction", "well-positioned", "optimistic")
_NEG = ("cautious", "challenging", "weak", "soft", "headwind", "pressure", "decline",
        "subdued", "muted", "difficult", "uncertain", "slowdown", "disappoint",
        "de-growth", "degrowth", "lower", "shortfall", "concern", "sluggish")

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")
_NUM = re.compile(r"\d")
_WS = re.compile(r"\s+")

# ── operational KPI extraction (Tijori-style, first layer) ───────────────────
# The numeric business metrics management cites on a call. Each entry: canonical
# metric → phrasings. We pull "<phrasing> … <number><unit>" within a short
# window; unit ∈ %, bps, x, crore/cr/lakh. Sector-agnostic — a name only shows
# the KPIs it actually mentioned. Facts extracted from the company's own
# transcript; not a full historical KPI database (that needs XBRL parsing).
_KPI_TERMS = [
    ("EBITDA margin", ("ebitda margin",)),
    ("Gross margin", ("gross margin",)),
    ("Operating margin", ("operating margin", "opm")),
    ("Net margin", ("net margin", "pat margin", "net profit margin")),
    ("ROE", ("return on equity", "roe")),
    ("ROCE", ("return on capital employed", "roce")),
    ("Capacity utilisation", ("capacity utilis", "capacity utiliz", "utilisation", "utilization")),
    ("Order book", ("order book", "order backlog", "orderbook")),
    ("AUM", ("assets under management", "aum")),
    ("NIM", ("net interest margin", "nim")),
    ("Gross NPA", ("gross npa", "gnpa")),
    ("Net NPA", ("net npa", "nnpa")),
    ("CASA ratio", ("casa ratio", "casa")),
    ("Credit growth", ("credit growth", "loan growth", "advances growth")),
    ("Deposit growth", ("deposit growth",)),
    ("Collection efficiency", ("collection efficiency",)),
    ("Same-store sales", ("same store sales", "sss", "like-for-like")),
    ("Realisation", ("realisation", "realization", "asp", "average selling price")),
    ("Volume growth", ("volume growth", "volume grew", "volumes grew")),
    ("Attrition", ("attrition",)),
    ("Utilisation (IT)", ("employee utilisation", "utilisation rate")),
    ("Capex", ("capex", "capital expenditure")),
    ("Net debt", ("net debt",)),
    ("Debt/Equity", ("debt to equity", "debt-equity", "d/e", "gearing")),
    ("Dividend payout", ("dividend payout", "payout ratio")),
]
_KPI_VALUE = re.compile(
    r"(?:of|at|was|were|stood at|is|are|around|about|~|reached|came in at|to)?\s*"
    r"(?:₹|rs\.?|inr)?\s*([\d,]+(?:\.\d+)?)\s*(%|per cent|percent|bps|basis points|x|times|"
    r"lakh crore|crore|cr\b|lakh)", re.I)


def _sentences(text: str) -> list[str]:
    text = _WS.sub(" ", text or "")
    return [s.strip() for s in _SENT_SPLIT.split(text) if 40 <= len(s.strip()) <= 320]


def _pick(sents, kws, need_number=False, cap=4, claimed=None) -> list[str]:
    """Pick up to `cap` distinct sentences hitting any keyword. `claimed` is a
    shared set of sentence fingerprints already used by a higher-priority
    bucket, so the same versatile sentence isn't shown three times."""
    out, seen = [], set()
    for s in sents:
        low = s.lower()
        if not any(k in low for k in kws):
            continue
        if need_number and not _NUM.search(s):
            continue
        fp = low[:70]
        if fp in seen or (claimed is not None and fp in claimed):
            continue
        seen.add(fp)
        if claimed is not None:
            claimed.add(fp)
        out.append(s)
        if len(out) >= cap:
            break
    return out


def extract_key_points(text: str) -> dict:
    """Deterministic key-point extraction. Returns structured pointers + a
    tone score in [-1, +1] (balance of confident vs cautious language) + a
    coarse guidance direction. Pure — unit-testable on any text."""
    sents = _sentences(text)
    low = (text or "").lower()

    pos = sum(low.count(w) for w in _POS)
    neg = sum(low.count(w) for w in _NEG)
    tone = round((pos - neg) / (pos + neg), 3) if (pos + neg) else 0.0

    # Priority order so a versatile sentence lands in its most salient bucket:
    # guidance → risks → margins → capex → demand.
    claimed: set = set()
    guidance = _pick(sents, _FORWARD, cap=5, claimed=claimed)
    risks = _pick(sents, _RISK, cap=4, claimed=claimed)
    margins = _pick(sents, _MARGIN, cap=3, claimed=claimed)
    capex = _pick(sents, _CAPEX, cap=3, claimed=claimed)
    demand = _pick(sents, _DEMAND, cap=3, claimed=claimed)

    # guidance direction from the tone of the guidance sentences only
    g_low = " ".join(guidance).lower()
    gpos = sum(g_low.count(w) for w in _POS)
    gneg = sum(g_low.count(w) for w in _NEG)
    direction = ("up" if gpos > gneg + 1 else "down" if gneg > gpos + 1
                 else "neutral") if guidance else None

    return {
        "guidance": guidance, "margins": margins, "capex": capex,
        "demand": demand, "risks": risks,
        "tone_score": tone,
        "tone_pos_hits": pos, "tone_neg_hits": neg,
        "guidance_direction": direction,
    }


def extract_kpis(text: str) -> list[dict]:
    """Pull the numeric operational metrics management cited. Returns
    [{metric, value, unit}] deduped to the first mention of each. Pure."""
    low = _WS.sub(" ", (text or "").lower())
    out, seen = [], set()
    for metric, phrasings in _KPI_TERMS:
        pos = -1
        for ph in phrasings:
            i = low.find(ph)
            if i != -1:
                pos = i + len(ph)
                break
        if pos == -1 or metric in seen:
            continue
        m = _KPI_VALUE.match(low[pos:pos + 40])          # value must follow closely
        if not m:
            # allow a few words between the phrase and the number
            m = _KPI_VALUE.search(low[pos:pos + 40])
        if not m:
            continue
        try:
            val = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        unit = m.group(2).lower()
        unit = ("%" if unit in ("%", "per cent", "percent") else
                "bps" if "bps" in unit or "basis" in unit else
                "x" if unit in ("x", "times") else
                "₹ lakh cr" if "lakh crore" in unit else
                "₹ lakh" if unit == "lakh" else "₹ cr")
        # sanity: percentages > 100 (except growth) or absurd values dropped
        if unit == "%" and val > 100 and "growth" not in metric.lower():
            continue
        seen.add(metric)
        out.append({"metric": metric, "value": round(val, 2), "unit": unit})
    return out


def _concalls_for(db, co) -> list[dict]:
    from app import models
    ins = db.query(models.CompanyInsight).filter_by(company_id=co.id).first()
    docs = (ins.data or {}).get("documents") if (ins and ins.data) else {}
    ccs = (docs or {}).get("concalls") or []
    return [c for c in ccs if isinstance(c, dict) and c.get("transcript")]


def ingest_one(db, co, with_llm: bool = False) -> str:
    """Process the latest transcript for one company. Returns a status string.
    Skips (no re-fetch) when the latest transcript URL is already stored."""
    from app import models
    ccs = _concalls_for(db, co)
    if not ccs:
        return "no-link"
    latest = ccs[0]
    url = latest.get("transcript")
    row = db.query(models.TranscriptInsight).filter_by(company_id=co.id).first()
    if row and row.source_url == url:
        return "current"

    from app.transcript_nlp import fetch_transcript_text
    text = fetch_transcript_text(url)
    if not text:
        return "no-text"
    points = extract_key_points(text)
    points["kpis"] = extract_kpis(text)          # operational metrics cited

    if not row:
        row = models.TranscriptInsight(company_id=co.id, ticker=co.ticker)
        db.add(row)
    row.quarter = latest.get("date")
    row.source_url = url
    row.tone_score = points.get("tone_score")
    row.points = points
    row.llm_summary = None          # LLM narrative retired — rules-based points only
    row.char_count = len(text)
    db.commit()
    return "processed"


def ingest_universe(db, limit: int = 120, with_llm: bool = False) -> dict:
    """Daily job: process transcripts for a bounded slice, oldest-processed
    first so the whole book cycles through within a week. Budget-friendly:
    only names with a transcript link and a not-yet-processed URL do real work."""
    from app import models
    from sqlalchemy import asc

    cos = (db.query(models.Company)
             .outerjoin(models.TranscriptInsight,
                        models.TranscriptInsight.company_id == models.Company.id)
             .order_by(asc(models.TranscriptInsight.processed_at).nullsfirst())
             .limit(limit * 3).all())        # over-fetch; most will be 'current'/'no-link'
    stats = {"processed": 0, "current": 0, "no_link": 0, "no_text": 0}
    done = 0
    for co in cos:
        if not co.ticker:
            continue
        try:
            r = ingest_one(db, co, with_llm=with_llm)
        except Exception as e:
            db.rollback()
            log.warning(f"transcript {co.ticker}: {type(e).__name__}: {e}")
            continue
        key = {"processed": "processed", "current": "current",
               "no-link": "no_link", "no-text": "no_text"}.get(r)
        if key:
            stats[key] += 1
        if r == "processed":
            done += 1
            if done >= limit:
                break
    log.info(f"transcript ingest: {stats}")
    return stats


def load(db, ticker: str) -> dict | None:
    from app import models
    row = (db.query(models.TranscriptInsight)
             .filter_by(ticker=ticker.upper()).first())
    if not row:
        return None
    return {"ticker": row.ticker, "quarter": row.quarter, "source_url": row.source_url,
            "tone_score": row.tone_score, "points": row.points or {},
            "llm_summary": row.llm_summary, "char_count": row.char_count,
            "processed_at": row.processed_at.isoformat() if row.processed_at else None}


def tone_by_ticker(db) -> dict[str, dict]:
    """{ticker: {tone, direction, quarter}} for the FM engine — the extracted
    management tone + guidance direction as a conviction input."""
    from app import models
    out = {}
    for r in db.query(models.TranscriptInsight).all():
        p = r.points or {}
        out[r.ticker] = {"tone": r.tone_score,
                         "direction": p.get("guidance_direction"),
                         "quarter": r.quarter}
    return out


# ── Rules-based earnings-call summary (NO LLM) ───────────────────────────────
# A readable narrative assembled deterministically from the same extractors that
# feed the concall-tone signal — guidance/margins/capex/demand/risks + the
# lexicon tone score + the numeric KPIs — plus a quarter-over-quarter tone shift
# computed from the prior call. 100% AI-free, zero API cost.
_SUMMARY_CACHE: dict[str, tuple[float, dict]] = {}
_SUMMARY_TTL = 3600 * 6

_SECTIONS = [("guidance", "Guidance & outlook"), ("margins", "Margins & costs"),
             ("capex", "Capex & expansion"), ("demand", "Demand & order book"),
             ("risks", "Key risks & watch-items")]


def _tone_word(t: float) -> str:
    return ("clearly confident" if t >= 0.5 else "confident" if t >= 0.2 else
            "broadly balanced" if t > -0.2 else "cautious" if t > -0.5 else
            "clearly cautious")


def _fmt_kpi(k: dict) -> str:
    u = k.get("unit") or ""
    sep = "" if u in ("%", "x") else " "
    return f"{k.get('metric')} — {k.get('value')}{sep}{u}".strip()


def build_summary_md(points: dict, kpis: list | None, prior_tone: float | None) -> str:
    """A markdown research-note-style summary from the deterministic extraction.
    Headers use '## ' so the frontend's renderMd styles them."""
    lines: list[str] = []
    tone = points.get("tone_score")
    pos, neg = points.get("tone_pos_hits"), points.get("tone_neg_hits")
    direction = points.get("guidance_direction")

    lines.append("## Management tone")
    if tone is not None:
        lead = f"Management struck a {_tone_word(tone)} tone (tone score {tone:+.2f}"
        if pos is not None and neg is not None:
            lead += f" — {pos} confident vs {neg} cautious cues"
        lead += ")."
        if direction and direction != "neutral":
            lead += f" The forward-guidance language skews {direction}."
        elif direction == "neutral":
            lead += " Forward guidance reads roughly balanced."
        lines.append(lead)
    else:
        lines.append("Tone could not be scored from the transcript text.")

    for key, title in _SECTIONS:
        rows = points.get(key) or []
        if rows:
            lines.append("")
            lines.append(f"## {title}")
            for s in rows[:5]:
                lines.append(f"• {s}")

    if kpis:
        lines.append("")
        lines.append("## Operational metrics cited")
        for k in kpis[:10]:
            lines.append(f"• {_fmt_kpi(k)}")

    lines.append("")
    lines.append("## Quarter-over-quarter shift")
    if prior_tone is not None and tone is not None:
        d = tone - prior_tone
        shift = ("more confident" if d > 0.1 else "more cautious" if d < -0.1
                 else "little changed")
        lines.append(f"Management tone is {shift} versus the prior call "
                     f"(this call {tone:+.2f} vs {prior_tone:+.2f}).")
    else:
        lines.append("Not enough transcript history for a quarter-over-quarter "
                     "comparison this time.")
    return "\n".join(lines)


def summarize_transcript(db, company_name: str, ticker: str,
                         force_refresh: bool = False) -> dict:
    """Rules-based summary of the latest earnings call (NO LLM). Prefers the
    stored TranscriptInsight (already extracted by the nightly ingester — instant);
    falls back to fetching + extracting the transcript on demand. Adds a
    quarter-over-quarter tone shift from the prior call, best-effort. Cached 6h."""
    from app import models
    import time as _t
    ticker = (ticker or "").upper()
    co = db.query(models.Company).filter_by(ticker=ticker).first()
    if not co:
        return {"ticker": ticker, "available": False, "message": "Unknown ticker."}
    ccs = _concalls_for(db, co)
    if not ccs:
        return {"ticker": ticker, "available": False,
                "message": "No concall transcript link is available for this company yet."}
    latest = ccs[0]
    url = latest.get("transcript")
    ck = f"{ticker}:{url}"
    if not force_refresh:
        c = _SUMMARY_CACHE.get(ck)
        if c and _t.time() - c[0] < _SUMMARY_TTL:
            return {**c[1], "cached": True}

    # Fast path: the nightly ingester already extracted this exact transcript.
    row = db.query(models.TranscriptInsight).filter_by(company_id=co.id).first()
    quarter = latest.get("date")
    if row and row.source_url == url and row.points:
        points = row.points
        quarter = row.quarter or quarter
    else:
        from app.transcript_nlp import fetch_transcript_text
        text = fetch_transcript_text(url)
        if not text:
            return {"ticker": ticker, "available": False, "quarter": quarter,
                    "message": "The transcript link could not be fetched or held "
                               "no extractable text."}
        points = extract_key_points(text)
        points["kpis"] = extract_kpis(text)

    # QoQ: extract the PRIOR call's tone for the shift line (best-effort — one
    # bounded fetch; skipped silently if unavailable).
    prior_tone = None
    if len(ccs) > 1 and ccs[1].get("transcript"):
        try:
            from app.transcript_nlp import fetch_transcript_text
            ptext = fetch_transcript_text(ccs[1]["transcript"])
            if ptext:
                prior_tone = extract_key_points(ptext).get("tone_score")
        except Exception:
            prior_tone = None

    summary = build_summary_md(points, points.get("kpis"), prior_tone)
    result = {"ticker": ticker, "available": True, "quarter": quarter,
              "has_prior": prior_tone is not None, "summary": summary,
              "tone_score": points.get("tone_score"), "method": "rules-based",
              "cached": False}
    _SUMMARY_CACHE[ck] = (_t.time(), result)
    return result
