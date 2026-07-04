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


def _dhan_error(e: Exception) -> str:
    """Compact error string that KEEPS Dhan's response body — that's where the
    real reason lives (e.g. DH-901 invalid client-id vs DH-808 no Data-plan
    subscription); httpx's status line alone can't tell them apart."""
    import httpx
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code}: {e.response.text[:220]}"
    return str(e)[:220]


@router.get("/dhan/status")
def dhan_status():
    """Diagnostics: is the token present, is client-id present, does the token
    actually authenticate (token-only historical probe), and did the instrument
    map load. Never exposes the secret values themselves."""
    from app.dhan import client, instruments
    tok_cid = client._client_id_from_token()
    env_cid = __import__("os").getenv("DHAN_CLIENT_ID", "").strip()
    out = {"configured": client.configured(),
           "has_client_id": bool(client.client_id()),
           # Which id the client actually sends, and whether the env var agrees
           # with the token's own claim (a mismatch here was the option-chain 401).
           "client_id_source": "token" if tok_cid else ("env" if env_cid else "none"),
           "env_client_id_matches_token": (env_cid == tok_cid) if (env_cid and tok_cid) else None,
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
                out["historical_probe"] = {"ok": False, "error": _dhan_error(e)}
            # Option-chain auth probe: needs token + client-id + F&O data access.
            # The full Dhan error body is surfaced so a failure self-diagnoses.
            try:
                exp = client.expiry_list(sid, seg="NSE_EQ")
                out["option_chain_probe"] = {"ok": exp is not None, "expiries": len(exp or [])}
            except Exception as e:
                out["option_chain_probe"] = {"ok": False, "error": _dhan_error(e)}
    return out


@router.get("/dhan/fno")
def fno_universe():
    """Tickers with listed stock futures/options (from the Dhan scrip master,
    cached ~daily). The frontend hides the Options tab for names not here.
    Empty list = master unavailable — callers should fail OPEN (show the tab)
    rather than hide options for everyone."""
    from app.dhan import instruments
    tks = sorted(instruments.fno_tickers())
    return {"count": len(tks), "tickers": tks}


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
                "message": f"Dhan expiry-list error: {_dhan_error(e)}"}
    if not expiries:
        return {"ticker": tk, "configured": True, "available": False,
                "message": "No option expiries for this name (may not be in F&O)."}
    chosen = expiry if (expiry and expiry in expiries) else expiries[0]
    try:
        chain = client.option_chain(sid, chosen, seg="NSE_EQ")
    except Exception as e:
        return {"ticker": tk, "configured": True, "available": False, "expiries": expiries,
                "expiry": chosen, "message": f"Dhan option-chain error: {_dhan_error(e)}"}
    return {"ticker": tk, "configured": True,
            "available": bool(chain and chain.get("strikes")),
            "expiries": expiries, "expiry": chosen, **(chain or {})}
