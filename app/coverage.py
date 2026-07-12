"""
app/coverage.py — per-name, per-tab data-coverage matrix for the whole universe.

Answers "which tab is empty for which company and why" in one query pass:
statements (Financials tab), facts (valuation inputs), insight blocks
(Analyst & Forward / description / ratios / documents), price freshness and
history depth. Powers the admin coverage endpoint and the targeted
fundamentals backfill (ingest exactly the names that are missing something,
never the ones that aren't).
"""
from __future__ import annotations

from sqlalchemy import func

from . import models


def coverage_rows(db, tickers) -> list[dict]:
    tset = {(t or "").upper() for t in tickers}
    cos = {c.id: c for c in db.query(models.Company).all()
           if (c.ticker or "").upper() in tset}

    stmt_counts = dict(db.query(models.HistoricalFinancial.company_id, func.count())
                         .group_by(models.HistoricalFinancial.company_id).all())
    fact_counts = dict(db.query(models.FinancialFact.company_id, func.count())
                         .group_by(models.FinancialFact.company_id).all())
    hist_counts = dict(db.query(models.HistoricalPrice.company_id, func.count())
                         .group_by(models.HistoricalPrice.company_id).all())
    snaps = {m.company_id: m for m in db.query(models.MarketSnapshot).all()}
    insights = {r.company_id: (r.data or {}) for r in db.query(models.CompanyInsight).all()}

    rows = []
    for cid, co in cos.items():
        ins = insights.get(cid) or {}
        rows.append({
            "ticker": (co.ticker or "").upper(),
            "sector": co.sector,
            "statements": stmt_counts.get(cid, 0),
            "facts": fact_counts.get(cid, 0),
            "history_rows": hist_counts.get(cid, 0),
            "has_price": snaps.get(cid) is not None,
            "has_insight": bool(ins),
            "has_analyst": bool(ins.get("analyst") or ins.get("target")),
            "has_forecasts": bool(ins.get("forecasts")),
            "has_ratios": bool(ins.get("ratios")),
            "has_documents": bool(ins.get("documents")),
            "has_results": bool(ins.get("results")),
        })
    rows.sort(key=lambda r: r["ticker"])
    return rows


def needs_fundamentals(db, tickers, min_statements: int = 4) -> list[str]:
    """Names whose Financials or Analyst tabs would be empty: fewer than
    `min_statements` statement rows OR no insight blob at all. These are the
    exact ingest targets for the fundamentals backfill."""
    return [r["ticker"] for r in coverage_rows(db, tickers)
            if r["statements"] < min_statements or not r["has_insight"]]


def summary(rows: list[dict]) -> dict:
    n = len(rows)
    def cnt(key, want=True):
        return sum(1 for r in rows if bool(r[key]) == want)
    return {
        "names": n,
        "missing_statements": sum(1 for r in rows if r["statements"] < 4),
        "missing_insight": cnt("has_insight", False),
        "missing_analyst": cnt("has_analyst", False),
        "missing_forecasts": cnt("has_forecasts", False),
        "missing_ratios": cnt("has_ratios", False),
        "missing_documents": cnt("has_documents", False),
        "missing_results": cnt("has_results", False),
        "missing_price": cnt("has_price", False),
        "thin_history": sum(1 for r in rows if r["history_rows"] < 200),
    }
