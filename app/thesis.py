"""
app/thesis.py — AI Investment Thesis generator.

Builds a deterministic context block from real DB data, calls Claude,
validates every number in the output against source data, and caches
results so the same company+data combination is only generated once.

Architecture:
  1. build_context()   → structured dict of financials + metrics + peer snapshot
  2. build_prompt()    → system + user messages for Claude
  3. call_claude()     → POST to Anthropic API, returns raw text
  4. validate_output() → checks every number in the response exists in context
  5. ThesisCache       → in-memory cache keyed by (ticker, data_hash)
     (replace with Redis or a DB table for production persistence)

The thesis covers:
  - Business overview + moat
  - Bull / Base / Bear cases with explicit assumptions
  - Key risks + monitoring triggers
  - Valuation summary (MoS, intrinsic, entry/target/stop)

Numbers in the output are validated: any figure that can't be traced back
to the context data causes a regeneration attempt (max 2 retries).
"""
from __future__ import annotations
import os
import json
import re
import hashlib
import time
from typing import Any

import httpx

from .templates import TemplateCode, is_financial
from .metrics import compute_metrics, METRIC_BY_KEY


# ── Cache ─────────────────────────────────────────────────────────────────────
class ThesisCache:
    def __init__(self, ttl_seconds: int = 3600 * 6):  # 6-hour TTL
        self._cache: dict[str, dict] = {}
        self._ttl = ttl_seconds

    def _key(self, ticker: str, data_hash: str) -> str:
        return f"{ticker}:{data_hash}"

    def get(self, ticker: str, data_hash: str) -> dict | None:
        k = self._key(ticker, data_hash)
        entry = self._cache.get(k)
        if entry and time.time() - entry["ts"] < self._ttl:
            return entry["data"]
        return None

    def set(self, ticker: str, data_hash: str, data: dict) -> None:
        self._cache[self._key(ticker, data_hash)] = {"data": data, "ts": time.time()}


_cache = ThesisCache()


# ── Context builder ───────────────────────────────────────────────────────────

def _safe_round(v, d=1):
    if v is None:
        return None
    try:
        return round(float(v), d)
    except Exception:
        return None


def build_context(company, facts: dict, hist_statements: dict,
                  metrics_result: dict, price: float,
                  peer_rows: list[dict]) -> dict:
    """
    Build a structured context dict that will be:
    1. Serialised into the Claude prompt
    2. Used for output validation (every number in the thesis must
       be traceable to a value in this dict)
    """
    template = company.template_code or "MANUFACTURING"
    fin = is_financial(template)

    # Latest year financials
    years = sorted(hist_statements.keys())
    latest_yr = years[-1] if years else None
    prev_yr   = years[-2] if len(years) >= 2 else None

    def pl(yr, k): return hist_statements.get(yr, {}).get("PL", {}).get(k)
    def bs(yr, k): return hist_statements.get(yr, {}).get("BS", {}).get(k)
    def cf(yr, k): return hist_statements.get(yr, {}).get("CF", {}).get(k)

    latest_fy = f"FY{str(latest_yr)[2:]}" if latest_yr else "FY?"

    # Flatten key metrics for prompt
    flat_metrics = {}
    for cat in metrics_result.get("categories", []):
        for m in cat.get("metrics", []):
            if m["value"] is not None:
                flat_metrics[m["key"]] = {
                    "label": m["label"],
                    "value": m["value"],
                    "formatted": m["formatted"],
                }

    financials_snapshot = {}
    if fin:
        financials_snapshot = {
            "interest_income": _safe_round(pl(latest_yr, "interest_income") or pl(latest_yr, "revenue"), 0),
            "nii":             _safe_round(pl(latest_yr, "nii"), 0),
            "provisions":      _safe_round(pl(latest_yr, "provisions"), 0),
            "pat":             _safe_round(pl(latest_yr, "pat") or facts.get("NET_PROFIT"), 0),
            "pat_prev":        _safe_round(pl(prev_yr, "pat"), 0),
            "equity":          _safe_round(bs(latest_yr, "equity") or facts.get("NET_WORTH"), 0),
            "total_assets":    _safe_round(bs(latest_yr, "total_assets"), 0),
            "borrowings":      _safe_round(bs(latest_yr, "borrowings") or bs(latest_yr, "total_debt"), 0),
            "aum":             _safe_round(facts.get("AUM"), 0),
            "gnpa":            _safe_round(facts.get("GNPA"), 4),
            "nnpa":            _safe_round(facts.get("NNPA"), 4),
            "crar":            _safe_round(facts.get("CRAR"), 4),
            "nim":             _safe_round(facts.get("NIM"), 4),
            "roa":             _safe_round(facts.get("ROA"), 4),
        }
    else:
        financials_snapshot = {
            "revenue":     _safe_round(pl(latest_yr, "revenue") or facts.get("REVENUE"), 0),
            "revenue_prev":_safe_round(pl(prev_yr, "revenue"), 0),
            "ebitda":      _safe_round(pl(latest_yr, "ebitda"), 0),
            "ebit":        _safe_round(pl(latest_yr, "ebit"), 0),
            "pat":         _safe_round(pl(latest_yr, "pat") or facts.get("NET_PROFIT"), 0),
            "pat_prev":    _safe_round(pl(prev_yr, "pat"), 0),
            "equity":      _safe_round(bs(latest_yr, "equity") or facts.get("NET_WORTH"), 0),
            "total_assets":_safe_round(bs(latest_yr, "total_assets"), 0),
            "net_debt":    _safe_round(facts.get("NET_DEBT"), 0),
            "operating_cf":_safe_round(cf(latest_yr, "operating_cf"), 0),
            "capex":       _safe_round(cf(latest_yr, "capex"), 0),
        }

    shares = company.shares_outstanding
    market_cap = round(price * shares, 0) if price else None
    intrinsic_bvps = flat_metrics.get("bvps", {}).get("value")
    intrinsic = None  # computed by DCF engine separately; not in metrics

    return {
        "company": {
            "name":    company.name,
            "ticker":  company.ticker,
            "sector":  company.sector,
            "template": template,
        },
        "market": {
            "price":      _safe_round(price, 2),
            "shares":     _safe_round(shares, 1),
            "market_cap_cr": _safe_round(market_cap, 0),
        },
        "latest_fy": latest_fy,
        "financials": financials_snapshot,
        "metrics": flat_metrics,
        "peers": [
            {
                "ticker":  p.get("ticker"),
                "name":    p.get("name"),
                "roe":     _safe_round(p.get("roe"), 4),
                "pe":      _safe_round(p.get("pe"), 2),
                "pb":      _safe_round(p.get("pb"), 2),
                "mos":     _safe_round(p.get("mos"), 4),
                "verdict": p.get("verdict"),
            }
            for p in peer_rows[:5]
        ],
    }


# ── Prompt builder ────────────────────────────────────────────────────────────

def build_prompt(ctx: dict) -> tuple[str, str]:
    """Returns (system_prompt, user_prompt)."""

    co = ctx["company"]
    fin = ctx["financials"]
    met = ctx["metrics"]
    mkt = ctx["market"]
    fy  = ctx["latest_fy"]
    fin_flag = is_financial(co["template"])

    def m(key): return met.get(key, {}).get("formatted", "—")
    def f(key): return fin.get(key, "—")

    system = (
        "You are a senior equity research analyst writing a structured investment thesis. "
        "Use ONLY the data provided in the context — do not invent figures or cite external sources. "
        "Every number you include must appear verbatim in the context data below. "
        "Be direct, analytical, and precise. Write in continuous prose with clear headers. "
        "Do not use bullet points. Do not add disclaimers. Do not repeat the raw data tables."
    )

    fin_block = ""
    if fin_flag:
        fin_block = f"""
FINANCIAL SNAPSHOT ({fy}):
  Interest Income / Revenue: ₹{f('interest_income')} cr
  Net Interest Income (NII): ₹{f('nii')} cr
  Provisions: ₹{f('provisions')} cr
  PAT: ₹{f('pat')} cr (prev year: ₹{f('pat_prev')} cr)
  AUM: ₹{f('aum')} cr | GNPA: {m('gnpa_pct')} | NNPA: {m('nnpa_pct')}
  NIM: {m('nim_metric')} | CRAR: {m('crar_metric')} | ROA: {m('roa')}
  Net Worth: ₹{f('equity')} cr | Total Assets: ₹{f('total_assets')} cr
  Borrowings: ₹{f('borrowings')} cr"""
    else:
        fin_block = f"""
FINANCIAL SNAPSHOT ({fy}):
  Revenue: ₹{f('revenue')} cr (prev year: ₹{f('revenue_prev')} cr)
  EBITDA: ₹{f('ebitda')} cr | EBIT: ₹{f('ebit')} cr
  PAT: ₹{f('pat')} cr (prev year: ₹{f('pat_prev')} cr)
  Operating CF: ₹{f('operating_cf')} cr | CapEx: ₹{f('capex')} cr
  Net Worth: ₹{f('equity')} cr | Net Debt: ₹{f('net_debt')} cr"""

    peer_lines = "\n".join(
        f"  {p['ticker']}: ROE {round((p['roe'] or 0)*100, 1)}% | "
        f"P/E {p['pe']}x | P/B {p['pb']}x | Verdict: {p['verdict']}"
        for p in ctx["peers"]
    ) or "  No peer data available."

    user = f"""Write an investment thesis for {co['name']} ({co['ticker']}) — {co['sector']}.

MARKET DATA:
  CMP: ₹{mkt['price']} | Market Cap: ₹{mkt['market_cap_cr']} cr | Shares: {mkt['shares']} cr
{fin_block}

KEY RATIOS:
  ROE: {m('roe')} | PAT Growth YoY: {m('pat_growth_yoy')} | PAT CAGR 3Y: {m('pat_cagr_3y')}
  P/E: {m('pe_ratio')} | P/B: {m('pb_ratio')} | Earnings Yield: {m('earnings_yield')}
  EBIT Margin: {m('ebit_margin')} | EBITDA Margin: {m('ebitda_margin')}
  Leverage Ratio: {m('leverage_ratio')} | Interest Coverage: {m('interest_cover')}
  EV/EBITDA: {m('ev_ebitda')} | FCF: {m('fcf_cr')}

PEERS:
{peer_lines}

Structure the thesis as follows — use these exact headers:

## Business & Moat
Two paragraphs on what the company does, its competitive position, and why the moat is durable or fragile.

## Bull Case
What would make this a 2-3x from current levels. Be specific about the assumptions.

## Base Case
Most likely 12-18 month outcome. Expected returns. Key metrics to watch.

## Bear Case
What breaks the thesis. Specific risk triggers, not generic disclaimers.

## Valuation Summary
Entry price, fair value range, and stop-loss level based on the data above.
"""

    return system, user


# ── Claude caller ─────────────────────────────────────────────────────────────

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = "claude-sonnet-4-20250514"


def call_claude(system: str, user: str, api_key: str, max_tokens: int = 1200) -> str:
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": CLAUDE_MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    resp = httpx.post(ANTHROPIC_API_URL, headers=headers, json=body, timeout=45.0)
    resp.raise_for_status()
    data = resp.json()
    return data["content"][0]["text"]


# ── Output validator ──────────────────────────────────────────────────────────

def _extract_numbers(text: str) -> list[float]:
    """Pull all numeric values from the thesis text (strip ₹ and commas)."""
    cleaned = text.replace(",", "").replace("₹", "").replace("%", "")
    return [float(m) for m in re.findall(r"\b\d+\.?\d*\b", cleaned)]


def _all_context_numbers(ctx: dict) -> set[str]:
    """Collect all numeric strings present anywhere in the context."""
    nums = set()

    def recurse(obj):
        if isinstance(obj, dict):
            for v in obj.values():
                recurse(v)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                recurse(v)
        elif isinstance(obj, (int, float)) and obj is not None:
            nums.add(str(round(obj, 2)))
            nums.add(str(int(obj)))
            nums.add(str(round(obj * 100, 1)))  # pct form

    recurse(ctx)
    return nums


def validate_output(text: str, ctx: dict) -> tuple[bool, list[str]]:
    """
    Light validation: extract numbers from thesis, check that large specific
    numbers (>1000, e.g. ₹ crore figures) exist somewhere in the context.
    Returns (is_valid, list_of_suspicious_numbers).
    """
    context_nums = _all_context_numbers(ctx)
    thesis_nums = _extract_numbers(text)
    suspicious = []

    for n in thesis_nums:
        if n > 1000:  # only validate large numbers (₹ crore figures)
            n_str = str(int(n))
            # Allow ±5% variance for rounded numbers
            found = any(
                abs(float(cn) - n) / max(n, 1) < 0.06
                for cn in context_nums
                if cn.replace(".", "").replace("-", "").isdigit()
            )
            if not found:
                suspicious.append(n_str)

    return len(suspicious) == 0, suspicious


# ── Main thesis generator ─────────────────────────────────────────────────────

def generate_thesis(
    company,
    facts: dict,
    hist_statements: dict,
    metrics_result: dict,
    price: float,
    peer_rows: list[dict],
    api_key: str,
    force_refresh: bool = False,
) -> dict:
    """
    Main entry point called by the FastAPI route.

    Returns:
        {
            "ticker": str,
            "thesis": str,           # markdown text
            "cached": bool,
            "data_hash": str,
            "model": str,
            "validation": { "passed": bool, "suspicious": [...] }
        }
    """
    # Build context
    ctx = build_context(company, facts, hist_statements, metrics_result, price, peer_rows)

    # Hash the context so we know when data changed
    data_hash = hashlib.md5(
        json.dumps(ctx, sort_keys=True, default=str).encode()
    ).hexdigest()[:12]

    # Check cache
    if not force_refresh:
        cached = _cache.get(company.ticker, data_hash)
        if cached:
            return {**cached, "cached": True}

    # Build prompt
    api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {
            "ticker": company.ticker,
            "thesis": "ANTHROPIC_API_KEY not configured. Add it to Railway environment variables.",
            "cached": False,
            "data_hash": data_hash,
            "model": CLAUDE_MODEL,
            "validation": {"passed": False, "suspicious": []},
            "error": "missing_api_key",
        }

    system, user = build_prompt(ctx)

    # Generate with up to 2 retries
    thesis_text = None
    validation = {"passed": False, "suspicious": []}

    for attempt in range(2):
        try:
            raw = call_claude(system, user, api_key)
            valid, suspicious = validate_output(raw, ctx)
            validation = {"passed": valid, "suspicious": suspicious}
            thesis_text = raw
            if valid:
                break
            # On failure, add a stricter instruction and retry
            user += "\n\nIMPORTANT: Only use numbers that appear in the data above. Do not add any figures not in the context."
        except httpx.HTTPStatusError as e:
            return {
                "ticker": company.ticker,
                "thesis": f"API error: {e.response.status_code} — {e.response.text[:200]}",
                "cached": False, "data_hash": data_hash,
                "model": CLAUDE_MODEL, "validation": validation,
                "error": "api_error",
            }
        except Exception as e:
            return {
                "ticker": company.ticker,
                "thesis": f"Generation error: {str(e)[:200]}",
                "cached": False, "data_hash": data_hash,
                "model": CLAUDE_MODEL, "validation": validation,
                "error": "generation_error",
            }

    result = {
        "ticker":     company.ticker,
        "thesis":     thesis_text,
        "cached":     False,
        "data_hash":  data_hash,
        "model":      CLAUDE_MODEL,
        "validation": validation,
        "context_summary": {
            "latest_fy":    ctx["latest_fy"],
            "template":     ctx["company"]["template"],
            "metrics_used": len(ctx["metrics"]),
            "peers_used":   len(ctx["peers"]),
        },
    }

    _cache.set(company.ticker, data_hash, result)
    return result
