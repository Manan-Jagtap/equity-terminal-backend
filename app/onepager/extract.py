"""
app/onepager/extract.py — extract structured NBFC quarterly data from the
Investor Presentation + Earnings Call Transcript via the Claude API.

Uses httpx + the raw Messages API, matching app/thesis.py's calling style
(x-api-key, anthropic-version 2023-06-01, model claude-sonnet-4-5). No SDK.

Flow:
    1. Look up the company's quarterly_documents (IP / TRANSCRIPT) for the
       quarter, download the PDF bytes from R2.
    2. Send the PDFs as base64 document blocks to Claude with a strict schema.
    3. Parse the JSON, attach company + source metadata, return a render-ready dict.

Accuracy posture: use ONLY figures present in the documents, never fabricate,
mark missing as null. Spot-check against the financials store before publishing.

Env: ANTHROPIC_API_KEY (required). ANTHROPIC_MODEL optional override.
"""
from __future__ import annotations
import base64
import json
import os
from datetime import date

import httpx

from app.database import SessionLocal
from app import models
from app.r2 import download_bytes
from app.onepager.brand import brand_for

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")
MAX_TOKENS = 6000
TIMEOUT = 180.0  # multi-MB PDFs take longer than a text call

_SYSTEM = """You are an equity research analyst extracting structured quarterly \
data for an Indian NBFC/lending company, to populate a one-page investor \
tear-sheet. You read the company's own Investor Presentation and Earnings Call \
Transcript and return STRICT JSON.

HARD RULES (accuracy is paramount):

PERIOD DISCIPLINE (most important — read carefully):
- This is a SINGLE-QUARTER tear-sheet. Every headline KPI and every section row \
must be the figure FOR THE TARGET QUARTER ITSELF (the standalone three-month \
period), NOT the full financial year.
- Q4 investor decks prominently feature FULL-YEAR (FY / annual / "12M") numbers \
alongside the quarter. You MUST NOT put a full-year figure in a quarterly slot. \
Example: if the deck shows "Q4 PAT Rs 1,508 cr" and "FY26 PAT Rs 10,134 cr", the \
PAT KPI is 1,508 — never 10,134.
- Flow metrics (PAT, NII, disbursements, revenue): use the QUARTERLY value. Stock \
metrics (AUM, borrowings, branches, CRAR, Stage-3): use the period-end value at \
quarter end.
- If a metric is ONLY available as a full-year number with no quarterly figure, set \
it to null — do NOT substitute the annual number.
- YoY for a quarterly metric = THIS QUARTER vs the SAME QUARTER LAST YEAR (e.g. \
Q4FY26 vs Q4FY25). NEVER report full-year-vs-full-year change as the quarterly YoY. \
QoQ = this quarter vs the immediately preceding quarter. If only a full-year growth \
rate is given, set the delta to null.

OTHER RULES:
- Use ONLY figures explicitly stated in the provided documents. Never estimate, \
infer, back-calculate, or fabricate a number. If a metric is not present, set \
its value to null and omit it — do not guess.
- Reproduce numbers exactly as reported (same unit and rounding). For absolute \
amounts prefer the company's reported unit (usually Rs crore).
- Deltas (YoY / QoQ): include ONLY when BOTH periods are explicitly given (e.g. \
in the IP's comparative tables). If only one period is shown, set the delta to \
null. Never compute a delta from a number you had to infer.
- Commentary quotes must be VERBATIM from the transcript, each under 25 words, \
and spoken by management. Choose the most decision-relevant remarks (guidance, \
growth outlook, asset quality, margins/cost of funds).
- key_takeaway: a factual 1-2 sentence synthesis of THE QUARTER (quarterly figures, \
not full-year). NO buy/sell/hold, NO valuation opinion, NO recommendation.

SOURCE PRIORITY: the Investor Presentation for hard quarterly numbers and \
comparatives; the Transcript for commentary/guidance and any figure only stated \
verbally.

DELTA DIRECTION ("dir" / "yoy_dir" / "qoq_dir") encodes COLOUR SEMANTICS:
- "up"   = favourable for shareholders (rendered green)
- "down" = unfavourable (rendered red)
- ""     = neutral / not applicable
For asset-quality and cost metrics (GNPA/Stage-3, NNPA, credit cost, cost of \
funds, cost-to-income), a RISING value is UNFAVOURABLE -> if it rose dir="down", \
if it fell dir="up". For growth, profitability, scale, coverage and capital \
metrics, a rising value is favourable.

Return ONLY the JSON object, no markdown fences, no commentary."""

_SCHEMA = """Return JSON with exactly this shape:

{
  "key_takeaway": "string (1-2 factual sentences, no rating)",
  "headline_kpis": [
    {"label":"short label","value":"formatted number string","unit":"e.g. Rs cr / % / null",
     "yoy":"+12.3%|-40bps|null","yoy_dir":"up|down|","qoq":"...|null","qoq_dir":"up|down|"}
  ],
  "profitability": [ {"label":"...","value":"...","delta":"...|null","dir":"up|down|"} ],
  "scale":         [ {"label":"...","value":"...","delta":"...|null","dir":"up|down|"} ],
  "asset_quality": [ {"label":"...","value":"...","delta":"...|null","dir":"up|down|"} ],
  "funding":       [ {"label":"...","value":"...","delta":"...|null","dir":"up|down|"} ],
  "commentary":    [ {"theme":"short label","quote":"verbatim <25 words"} ],
  "fy_callout": {
    "label":"e.g. FY26 (Full Year)",
    "items":[ {"label":"short label","value":"formatted number","delta":"+xx% YoY|null","dir":"up|down|"} ]
  },
  "peer_metrics": {
    "aum":"e.g. 1,02,400 cr|null", "aum_growth_yoy":"+24.1%|null",
    "nim":"11.6%|null", "roa":"5.1%|null", "roe":"19.4%|null",
    "gs3":"3.6%|null", "cof":"8.7%|null"
  }
}

Provide 5 headline_kpis (the most important: typically AUM, PAT, NIM, RoA, and a \
key asset-quality metric) — all QUARTERLY. Provide as many rows per section as \
the documents support (aim 4-6 each), all QUARTERLY. Provide 2-3 commentary \
items.

"fy_callout" holds the FULL-YEAR (annual / FY) headline figures the deck itself \
emphasises for the year — typically 3 to 4 of them (e.g. full-year PAT, \
full-year AUM or AUM growth, full-year RoA/RoE, total income). Use whatever the \
deck headlines as its annual highlights; do not force a fixed set. These are the \
ONLY place full-year numbers belong. Label the period in "label" (e.g. "FY26 \
(Full Year)"). If the deck shows no distinct full-year highlights (e.g. a pure \
Q1/Q2/Q3 quarter), set "fy_callout" to null.

"peer_metrics" holds \
canonical values for cross-company comparison, using the SAME figures you \
reported above (null if not available); keep AUM in Rs crore. If the company \
filed no Investor Presentation and only a transcript is available, extract \
whatever figures the transcript states, rely more on commentary, and set \
unavailable metrics to null."""


def _doc_block(pdf_bytes: bytes, title: str, cache: bool = False) -> dict:
    block = {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": base64.b64encode(pdf_bytes).decode(),
        },
        "title": title,
    }
    if cache:
        block["cache_control"] = {"type": "ephemeral"}
    return block


def _strip_json(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        if t.startswith("json"):
            t = t[4:]
    a, b = t.find("{"), t.rfind("}")
    if a != -1 and b != -1:
        t = t[a:b + 1]
    return t.strip()


def _fetch_docs(db, ticker: str, quarter: str):
    co = db.query(models.Company).filter(models.Company.ticker == ticker.upper()).first()
    if not co:
        raise ValueError(f"company not found: {ticker}")
    rows = (db.query(models.QuarterlyDocument)
              .filter(models.QuarterlyDocument.company_id == co.id,
                      models.QuarterlyDocument.quarter == quarter.upper(),
                      models.QuarterlyDocument.doc_type.in_(["IP", "TRANSCRIPT"]))
              .all())
    out = {}
    for r in rows:
        data = download_bytes(r.r2_key)
        if data:
            out[r.doc_type] = (data, r)
    return co, out


def _call_claude_docs(content_blocks: list, max_retries: int = 5) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": CLAUDE_MODEL,
        "max_tokens": MAX_TOKENS,
        "system": _SYSTEM,
        "messages": [{"role": "user", "content": content_blocks}],
    }
    import time
    attempt = 0
    while True:
        attempt += 1
        resp = httpx.post(ANTHROPIC_API_URL, headers=headers, json=body, timeout=TIMEOUT)
        # Retry on 429 (rate limit) and 529 (overloaded) with backoff.
        if resp.status_code in (429, 529) and attempt <= max_retries:
            retry_after = resp.headers.get("retry-after")
            if retry_after and retry_after.replace(".", "").isdigit():
                wait = float(retry_after)
            else:
                wait = min(60, 5 * (2 ** (attempt - 1)))
            print(f"    rate-limited ({resp.status_code}); retry {attempt}/{max_retries} "
                  f"in {wait:.0f}s…", flush=True)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        data = resp.json()
        return "".join(b.get("text", "") for b in data.get("content", [])
                       if b.get("type") == "text")


def extract_company_quarter(ticker: str, quarter: str) -> dict:
    db = SessionLocal()
    try:
        co, docs = _fetch_docs(db, ticker, quarter)
        if not docs:
            raise ValueError(f"no IP/TRANSCRIPT stored for {ticker} {quarter}")

        content = []
        if "IP" in docs:
            content.append(_doc_block(docs["IP"][0], "Investor Presentation", cache=True))
        if "TRANSCRIPT" in docs:
            content.append(_doc_block(docs["TRANSCRIPT"][0], "Earnings Call Transcript"))
        qn = quarter.upper()
        qnum = qn[:2] if qn.startswith("Q") else qn
        fyr = qn[2:] if "FY" in qn else ""
        prev_fy = ""
        if fyr.startswith("FY") and fyr[2:].isdigit():
            prev_fy = "FY" + str(int(fyr[2:]) - 1).zfill(len(fyr[2:]))
        period_note = (
            f"TARGET PERIOD: {qnum} of {fyr} (the standalone three-month quarter). "
            f"Report {qnum} {fyr} quarterly figures only. For YoY compare against "
            f"{qnum} {prev_fy} (same quarter previous year), NOT full-year vs full-year. "
            f"Do NOT use full-year totals as headline values."
        )
        content.append({"type": "text", "text":
            f"Company: {co.name} ({ticker.upper()}).\n{period_note}\n\n{_SCHEMA}"})

        raw = _call_claude_docs(content)
        data = json.loads(_strip_json(raw))

        b = brand_for(ticker)
        q = quarter.upper()
        qlabel = q.replace("FY", " FY")
        sources = []
        for dt, (_, row) in docs.items():
            fd = row.bse_filing_date.strftime("%d %b %Y") if getattr(row, "bse_filing_date", None) else "n/a"
            label = "Investor Presentation" if dt == "IP" else "Earnings Call Transcript"
            sources.append({"doc_type": label, "filing_date": fd})

        return {
            "company": {
                "name": co.name,
                "ticker": ticker.upper(),
                "exchange": "NSE/BSE",
                "sector": co.sector or "NBFC",
                "subsector": b["subsector"],
                "brand_color": b["color"],
            },
            "quarter": q,
            "quarter_label": qlabel,
            "as_of": date.today().strftime("%d %b %Y"),
            "key_takeaway": data.get("key_takeaway", ""),
            "headline_kpis": data.get("headline_kpis", []),
            "left_sections": [
                {"title": "Profitability", "rows": data.get("profitability", [])},
                {"title": "Scale & Franchise", "rows": data.get("scale", [])},
            ],
            "right_sections": [
                {"title": "Asset Quality", "rows": data.get("asset_quality", [])},
                {"title": "Funding & Capital", "rows": data.get("funding", [])},
            ],
            "commentary": data.get("commentary", []),
            "fy_callout": data.get("fy_callout"),
            "sources": sources,
            "peers": None,
            "peer_metrics": data.get("peer_metrics", {}),
            "_raw_extraction": data,
        }
    finally:
        db.close()
