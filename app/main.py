"""
FastAPI application.

Endpoints (all under /api):
  GET  /api/health                       liveness
  GET  /api/companies                    screener rows (computed verdict/score/intrinsic)
  GET  /api/companies/{ticker}           full detail: fundamentals + technicals + valuation + verdict
  POST /api/companies/{ticker}/valuation recompute with user-tweaked assumptions (the sliders)

Run locally:  uvicorn app.main:app --reload
"""
import os
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from .database import Base, engine, get_db
from . import models, engines
from .assemble import build_company, assumptions_dict
from .schemas import AssumptionOverride

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Equity Research Terminal API", version="1.0")

# Allow the frontend to call us. In production set FRONTEND_ORIGIN to your
# Vercel URL; "*" is fine while developing.
origins = os.getenv("FRONTEND_ORIGIN", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origins] if origins != "*" else ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/companies")
def list_companies(db: Session = Depends(get_db)):
    rows = []
    for co in db.query(models.Company).all():
        data = build_company(db, co)
        a = assumptions_dict(co.assumptions)
        rec = engines.recommend(data, a)
        rows.append({
            "ticker": co.ticker, "name": co.name, "sector": co.sector, "type": co.type,
            "price": data["price"], "intrinsic": rec["valuation"]["intrinsic"],
            "mos": rec["mos"], "roe": rec["fundamentals"]["roe"],
            "pb": rec["fundamentals"]["pb"], "pe": rec["fundamentals"]["pe"],
            "composite": rec["composite"], "verdict": rec["verdict"],
        })
    rows.sort(key=lambda r: r["composite"], reverse=True)
    return rows


def _get_or_404(db, ticker):
    co = db.query(models.Company).filter_by(ticker=ticker.upper()).first()
    if not co:
        raise HTTPException(404, f"Unknown ticker {ticker}")
    return co


@app.get("/api/companies/{ticker}")
def company_detail(ticker: str, db: Session = Depends(get_db)):
    co = _get_or_404(db, ticker)
    data = build_company(db, co)
    a = assumptions_dict(co.assumptions)
    rec = engines.recommend(data, a)
    sens = engines.sensitivity(data, a)
    return {"company": _public(data), "assumptions": a, "recommendation": rec, "sensitivity": sens}


@app.post("/api/companies/{ticker}/valuation")
def recompute(ticker: str, override: AssumptionOverride, db: Session = Depends(get_db)):
    co = _get_or_404(db, ticker)
    data = build_company(db, co)
    a = assumptions_dict(co.assumptions)

    payload = override.dict(exclude_none=True)
    if "price" in payload:
        data["price"] = payload.pop("price")
    a.update(payload)

    rec = engines.recommend(data, a)
    sens = engines.sensitivity(data, a)
    return {"company": _public(data), "assumptions": a, "recommendation": rec, "sensitivity": sens}


def _public(data: dict) -> dict:
    """Strip the heavy price series from the company payload (technicals carry it)."""
    return {k: v for k, v in data.items() if k != "series"}
