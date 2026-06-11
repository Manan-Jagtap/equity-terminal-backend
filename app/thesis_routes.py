"""
app/thesis_routes.py — AI research note, wiring app.thesis's existing pipeline.

  GET /api/companies/{ticker}/thesis → {"status":"ok","thesis":…, "generated_at":…}

Always HTTP 200:
  - no ANTHROPIC_API_KEY      → {"status":"unavailable","reason":…}
  - unknown ticker / failure  → {"status":"error","reason":…}

The context is built from real DB data exactly as thesis.build_context expects:
latest facts, the 5-year shaped statements, the computed metrics block, the
live price and a same-sector peer snapshot (from precomputed Valuation rows).
thesis.generate_thesis handles caching internally (keyed by ticker+data hash),
so repeated reads with unchanged data never re-call Claude.
"""
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app import models
from app.assemble import latest_facts, _shape_statements
from app.thesis import generate_thesis

router = APIRouter(prefix="/api", tags=["thesis"])


def _peer_rows(db: Session, co: models.Company) -> list[dict]:
    """Same-sector peer snapshot from precomputed Valuation rows (nullable-safe)."""
    out = []
    try:
        val_by = {v.company_id: v for v in db.query(models.Valuation).all()}
        peers = (db.query(models.Company)
                   .filter(models.Company.sector == co.sector,
                           models.Company.id != co.id).all())
        for p in peers:
            v = val_by.get(p.id)
            if not v:
                continue
            out.append({"ticker": p.ticker, "name": p.name, "roe": v.roe,
                        "pe": v.pe, "pb": v.pb, "mos": v.mos, "verdict": v.verdict})
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
    return out[:5]


@router.get("/companies/{ticker}/thesis")
def company_thesis(ticker: str, refresh: bool = Query(False),
                   db: Session = Depends(get_db)):
    api_key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        return {"status": "unavailable", "reason": "ANTHROPIC_API_KEY not configured"}

    try:
        co = db.query(models.Company).filter_by(ticker=ticker.upper()).first()
        if not co:
            return {"status": "error", "reason": f"Unknown ticker {ticker}"}

        facts = latest_facts(db, co.id)
        hist_rows = (db.query(models.HistoricalFinancial)
                       .filter_by(company_id=co.id).all())
        hist_statements = _shape_statements(hist_rows)
        price = co.market.price if co.market else None
        template = co.template_code or "MANUFACTURING"

        metrics_result = {"categories": []}
        try:
            from app.metrics import compute_metrics
            metrics_result = compute_metrics(co, facts, hist_statements,
                                             price or 0.0, template)
        except Exception:
            pass

        result = generate_thesis(
            company=co,
            facts=facts,
            hist_statements=hist_statements,
            metrics_result=metrics_result,
            price=price or 0.0,
            peer_rows=_peer_rows(db, co),
            api_key=api_key,
            force_refresh=refresh,
        )
        if result.get("error") == "missing_api_key":
            return {"status": "unavailable", "reason": "ANTHROPIC_API_KEY not configured"}
        if result.get("error"):
            return {"status": "error", "reason": result.get("thesis") or result["error"]}

        return {
            "status": "ok",
            "thesis": result.get("thesis"),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "cached": bool(result.get("cached")),
            "model": result.get("model"),
            "validation": result.get("validation"),
        }
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        return {"status": "error", "reason": str(e)}
