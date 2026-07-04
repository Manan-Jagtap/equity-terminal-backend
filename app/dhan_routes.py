"""
app/dhan_routes.py — Dhan-backed endpoints (options analytics + status).

  GET /api/dhan/status                    → is Dhan configured + instrument coverage
  GET /api/companies/{ticker}/options     → option chain (nearest or ?expiry=) with
                                            OI / IV / greeks / bid-ask + PCR

All degrade gracefully to a clear "not configured / unavailable" payload — never
a 500 — so the frontend can show an honest empty state.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app import models

router = APIRouter(prefix="/api", tags=["dhan"])


@router.get("/dhan/status")
def dhan_status():
    """Diagnostics: is the token present, is client-id present, does the token
    actually authenticate (token-only historical probe), and did the instrument
    map load. Never exposes the secret values themselves."""
    from app.dhan import client, instruments
    out = {"configured": client.configured(),
           "has_client_id": bool(client.client_id()),
           "instruments": instruments.coverage()}
    if client.configured():
        sid = instruments.security_id("RELIANCE")
        out["reliance_security_id"] = sid
        if sid:
            import datetime as _dt
            to = _dt.date.today()
            frm = to - _dt.timedelta(days=8)
            try:
                rows = client.historical_daily(sid, frm.isoformat(), to.isoformat())
                out["historical_probe"] = {"ok": rows is not None, "rows": len(rows or [])}
            except Exception as e:
                out["historical_probe"] = {"ok": False, "error": str(e)[:180]}
    return out


@router.get("/companies/{ticker}/options")
def company_options(ticker: str, expiry: str | None = None, db: Session = Depends(get_db)):
    from app.dhan import client, instruments
    tk = ticker.upper()
    if not client.configured():
        return {"ticker": tk, "configured": False,
                "message": "Options need Dhan — set DHAN_ACCESS_TOKEN on the backend."}
    sid = instruments.security_id(tk)
    if not sid:
        return {"ticker": tk, "configured": True, "available": False,
                "message": f"No Dhan security-id mapping for {tk} yet."}
    try:
        expiries = client.expiry_list(sid, seg="NSE_EQ") or []
    except Exception as e:
        return {"ticker": tk, "configured": True, "available": False,
                "message": f"Dhan expiry-list error: {str(e)[:140]}"}
    if not expiries:
        return {"ticker": tk, "configured": True, "available": False,
                "message": "No option expiries for this name (may not be in F&O)."}
    chosen = expiry if (expiry and expiry in expiries) else expiries[0]
    try:
        chain = client.option_chain(sid, chosen, seg="NSE_EQ")
    except Exception as e:
        return {"ticker": tk, "configured": True, "available": False, "expiries": expiries,
                "expiry": chosen, "message": f"Dhan option-chain error: {str(e)[:140]}"}
    return {"ticker": tk, "configured": True,
            "available": bool(chain and chain.get("strikes")),
            "expiries": expiries, "expiry": chosen, **(chain or {})}
