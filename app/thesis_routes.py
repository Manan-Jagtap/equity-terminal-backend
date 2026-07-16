"""
app/thesis_routes.py — AI research-note endpoint (RETIRED).

  GET /api/companies/{ticker}/thesis → {"status":"unavailable","reason":…}

The LLM narrative was retired to keep the platform 100% AI-free (no paid API).
The frontend already treats a "unavailable" status as "hide the AI Thesis tab",
so this keeps the route contract intact while returning nothing to render. The
rules-based read lives in the Verdict / Forensics / Scorecard tabs.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db

router = APIRouter(prefix="/api", tags=["thesis"])


@router.get("/companies/{ticker}/thesis")
def company_thesis(ticker: str, db: Session = Depends(get_db)):
    return {"status": "unavailable",
            "reason": ("AI thesis retired — the rules-based read is in the "
                       "Verdict / Forensics / Scorecard tabs.")}
