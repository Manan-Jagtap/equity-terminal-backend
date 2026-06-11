"""
app/documents_routes.py — company documents (concalls, annual reports,
credit ratings, announcements) from the stored insight blob.

  GET /api/companies/{ticker}/documents → the insight's normalised "documents"
                                          dict, or {} — NEVER a 500. The data is
                                          populated by the ingester's _documents()
                                          best-effort call to IndianAPI /documents.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app import models

router = APIRouter(prefix="/api", tags=["documents"])


@router.get("/companies/{ticker}/documents")
def company_documents(ticker: str, db: Session = Depends(get_db)):
    try:
        co = db.query(models.Company).filter_by(ticker=ticker.upper()).first()
        if not co:
            return {}
        ins = db.query(models.CompanyInsight).filter_by(company_id=co.id).first()
        docs = (ins.data or {}).get("documents") if ins else None
        return docs if isinstance(docs, dict) else {}
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return {}
