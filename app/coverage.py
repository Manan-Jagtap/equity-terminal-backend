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


# DATA-01/FIX-03: tickers a prior ingest proved the vendor cannot resolve to the
# right company (its /stock name-search returns a DIFFERENT security's numbers —
# VAML and VISL both resolved to a single micro Vedanta-segment entity). Held in
# KVStore, not a Company column, so no migration is needed and an admin can clear
# it. Excluded from `needs_fundamentals` so the daily backfill stops burning
# quota retrying a name that will never converge.
VENDOR_UNRESOLVED_KEY = "vendor_unresolved_v1"


def vendor_unresolved_set(db) -> set[str]:
    row = db.query(models.KVStore).filter_by(key=VENDOR_UNRESOLVED_KEY).first()
    data = (row.value if (row and isinstance(row.value, dict)) else None) or {}
    return {(t or "").upper() for t in (data.get("tickers") or [])}


def mark_vendor_unresolved(db, ticker: str, vendor_symbol: str | None = None) -> None:
    """Record a proven identity mismatch. Idempotent; stores the wrong symbol the
    vendor returned for diagnostics. Committed here — callers stay simple."""
    from sqlalchemy.orm.attributes import flag_modified
    tk = (ticker or "").upper()
    if not tk:
        return
    row = db.query(models.KVStore).filter_by(key=VENDOR_UNRESOLVED_KEY).first()
    data = dict(row.value) if (row and isinstance(row.value, dict)) else {}
    tks = {(t or "").upper() for t in (data.get("tickers") or [])}
    tks.add(tk)
    data["tickers"] = sorted(tks)
    detail = dict(data.get("detail") or {})
    detail[tk] = vendor_symbol
    data["detail"] = detail
    if row:
        row.value = data
        flag_modified(row, "value")
    else:
        db.add(models.KVStore(key=VENDOR_UNRESOLVED_KEY, value=data))
    db.commit()


def clear_vendor_unresolved(db, ticker: str) -> bool:
    """Drop a ticker from the unresolved set (e.g. after its alias is fixed).
    Returns True if it was present. Lets a corrected name re-enter the backfill."""
    from sqlalchemy.orm.attributes import flag_modified
    tk = (ticker or "").upper()
    row = db.query(models.KVStore).filter_by(key=VENDOR_UNRESOLVED_KEY).first()
    if not row or not isinstance(row.value, dict):
        return False
    data = dict(row.value)
    tks = {(t or "").upper() for t in (data.get("tickers") or [])}
    if tk not in tks:
        return False
    tks.discard(tk)
    data["tickers"] = sorted(tks)
    detail = dict(data.get("detail") or {})
    detail.pop(tk, None)
    data["detail"] = detail
    row.value = data
    flag_modified(row, "value")
    db.commit()
    return True


def needs_fundamentals(db, tickers, min_statements: int = 4) -> list[str]:
    """Names whose Financials or Analyst tabs would be empty: fewer than
    `min_statements` statement rows OR no insight blob at all. These are the
    exact ingest targets for the fundamentals backfill — EXCEPT names proven
    vendor-unresolvable (DATA-01), which are skipped so the loop converges."""
    unresolved = vendor_unresolved_set(db)
    return [r["ticker"] for r in coverage_rows(db, tickers)
            if (r["statements"] < min_statements or not r["has_insight"])
            and r["ticker"].upper() not in unresolved]


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
