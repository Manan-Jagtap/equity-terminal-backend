from fastapi import Depends
from app.admin_routes import require_admin
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
def dhan_status(_admin=Depends(require_admin)):
    """Diagnostics: is the token present, is client-id present, does the token
    actually authenticate (token-only historical probe), and did the instrument
    map load. Never exposes the secret values themselves.

    ADMIN ONLY. This is owner diagnostics — the docstring always said so — but it
    was unauthenticated, and each call makes several LIVE broker requests
    (token probe + instrument-map load). Anonymous traffic could therefore stall
    worker threads on someone else's slow API and burn the owner's broker quota,
    with no account needed. It reveals nothing secret, but it spends real
    resources on behalf of an unauthenticated caller."""
    from app.market_data import client, instruments, PROVIDER
    # PROVIDER-AWARE. This endpoint was written when Dhan was the only vendor and
    # reached straight past the shared interface into Dhan-private internals
    # (_client_id_from_token, _post, the client-id header matrix). After the
    # 7 Sep 2026 swap those attributes do not exist on app/upstox/client.py, so
    # the FIRST line 500'd — an admin diagnostic that failed exactly when you
    # would reach for it, in a module whose own docstring promises "never a 500".
    # Everything below now goes through the shared interface, and the
    # Dhan-specific probes are gated on the active provider AND hasattr.
    _is_dhan = PROVIDER == "dhan"
    tok_cid = client._client_id_from_token() if hasattr(client, "_client_id_from_token") else None
    env_cid = __import__("os").getenv("DHAN_CLIENT_ID", "").strip() if _is_dhan else ""
    # Token expiry from the JWT's own exp claim. Dhan's rotates ~daily and a
    # stale one is the first suspect on 401/808; Upstox's Analytics token is
    # 1-year, so a near expiry there is a renewal reminder rather than a bug.
    tok_exp = None
    try:
        import base64 as _b64, json as _json, datetime as _dtt
        body = client.access_token().split(".")[1]
        body += "=" * (-len(body) % 4)
        exp = _json.loads(_b64.urlsafe_b64decode(body)).get("exp")
        if exp:
            tok_exp = _dtt.datetime.fromtimestamp(int(exp), _dtt.timezone.utc).isoformat()
    except Exception:
        pass
    out = {"provider": PROVIDER,
           "configured": client.configured(),
           "token_expires_utc": tok_exp,
           "has_client_id": bool(client.client_id()),
           # Which id the client actually sends, and whether the env var agrees
           # with the token's own claim (a mismatch here was the option-chain
           # 401). Dhan-only: Upstox carries identity inside the bearer token
           # and sends no client-id header, so these read None rather than
           # inventing a verdict about a header that is not used.
           "client_id_source": ("token" if tok_cid else ("env" if env_cid else "none")) if _is_dhan else None,
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
            # Batch-LTP probe through the SHARED interface. This used to call
            # client._post directly with int(sid) so it could vary the client-id
            # header (the live feed once 401'd while the chain authenticated).
            # Both of those are Dhan-shaped: Upstox has no _post, sends no
            # client-id, and its ids are strings like "NSE_EQ|INE002A01018" that
            # int() cannot parse. ltp_quote() answers the question that actually
            # matters — "can this provider price a name right now?" — on either.
            try:
                q = client.ltp_quote({"NSE_EQ": [sid]})
                got = (q or {}).get("NSE_EQ") or {}
                px = got.get(str(sid))
                out["ltp_probe"] = {"ok": px is not None, "price": px}
            except Exception as e:
                out["ltp_probe"] = {"ok": False, "error": _dhan_error(e)[:120]}
            # Dhan's client-id header matrix, kept for when MARKET_DATA_PROVIDER
            # is flipped back: which id (token claim / env / none) the quote
            # endpoint family actually accepts. Meaningless under Upstox.
            # Every private this block touches is hasattr-checked by name, not
            # just _post — tests/test_market_data_provider_parity.py requires it,
            # and a provider-string gate alone would not survive someone adding
            # a third vendor that reports PROVIDER == "dhan"-ish by accident.
            if (_is_dhan and hasattr(client, "_post")
                    and hasattr(client, "_client_id_from_token")
                    and hasattr(client, "_quote_rl")):
                import os as _os
                variants = {"token_claim": client._client_id_from_token(),
                            "env": _os.getenv("DHAN_CLIENT_ID", "").strip(),
                            "none": None}
                matrix = {}
                for label, cid in variants.items():
                    try:
                        r = client._post("/marketfeed/ltp", {"NSE_EQ": [int(sid)]},
                                         client._quote_rl,
                                         extra_headers=({"client-id": cid} if cid else None))
                        matrix[label] = {"ok": bool(((r or {}).get("data") or {}).get("NSE_EQ") or {})}
                    except Exception as e:
                        matrix[label] = {"ok": False, "error": _dhan_error(e)[:120]}
                out["client_id_matrix"] = matrix
    return out


@router.get("/live")
def live_prices():
    """Near-real-time prices for the visible universe + headline indices.
    Served from a ~12s cache during market hours — all clients share one
    upstream Dhan batch-LTP call per window. Poll every ~15s client-side."""
    from app.live_prices import snapshot
    return snapshot()


_IDX_HIST_CACHE: dict = {}
_IDX_HIST_TTL = 3600.0


@router.get("/indices/{name}/history")
def index_history(name: str, years: int = 5):
    """Daily history for an NSE index (clicking an index card on the dashboard
    opens its chart). Name is the display name ("NIFTY 50", "NIFTY Bank", …) —
    resolved to a Dhan securityId via instruments.resolve_index. Cached 1h per
    index; degrades to an honest message for unresolvable names (e.g. SENSEX,
    a BSE index) or when Dhan is unconfigured."""
    import datetime as _dt
    import time as _time
    from app.market_data import client, instruments
    if not client.configured():
        return {"index": name, "available": False,
                "message": "Index history needs the market-data feed."}
    sid = instruments.index_security_id(name)
    if not sid:
        return {"index": name, "available": False,
                "message": f"No NSE index series for “{name}” — BSE indices aren't covered yet."}
    years = max(1, min(int(years or 5), 10))
    key = (sid, years)
    hit = _IDX_HIST_CACHE.get(key)
    if hit and _time.time() - hit[0] < _IDX_HIST_TTL:
        return hit[1]
    to = _dt.date.today()
    frm = to - _dt.timedelta(days=365 * years + 7)
    try:
        rows = client.historical_daily(sid, frm.isoformat(), to.isoformat(),
                                       exchange_segment="IDX_I", instrument="INDEX")
    except Exception as e:
        return {"index": name, "available": False,
                "message": f"Dhan index-history error: {_dhan_error(e)}"}
    out = {"index": name, "available": bool(rows), "count": len(rows or []),
           "data": rows or []}
    if rows:
        _IDX_HIST_CACHE[key] = (_time.time(), out)
    return out


@router.get("/dhan/fno")
def fno_universe():
    """Tickers with listed stock futures/options (from the Dhan scrip master,
    cached ~daily). The frontend hides the Options tab for names not here.
    Empty list = master unavailable — callers should fail OPEN (show the tab)
    rather than hide options for everyone."""
    from app.market_data import instruments
    tks = sorted(instruments.fno_tickers())
    return {"count": len(tks), "tickers": tks}


@router.get("/companies/{ticker}/options")
def company_options(ticker: str, expiry: str | None = None, db: Session = Depends(get_db)):
    from app.market_data import client, instruments, PROVIDER
    feed = PROVIDER.capitalize()
    # `feed_provider` rides on EVERY branch — the frontend caption used to say
    # "Live option chain via Dhan" as a hardcoded string and kept saying it for
    # a day after the backend moved to Upstox (7 Sep 2026). Naming the vendor
    # here once, on every response shape, is what lets OptionsTab.jsx stop
    # hardcoding it — see the vendor-swap note in app/market_data.py.
    tk = ticker.upper()
    if not client.configured():
        return {"ticker": tk, "configured": False, "feed_provider": PROVIDER,
                "message": "Options need the market-data feed."}
    sid = instruments.security_id(tk)
    if not sid:
        return {"ticker": tk, "configured": True, "available": False, "feed_provider": PROVIDER,
                "message": f"No {feed} security-id mapping for {tk} yet."}
    try:
        expiries = client.expiry_list(sid, seg="NSE_EQ") or []
    except Exception as e:
        return {"ticker": tk, "configured": True, "available": False, "feed_provider": PROVIDER,
                "message": f"{feed} expiry-list error: {_dhan_error(e)}"}
    if not expiries:
        return {"ticker": tk, "configured": True, "available": False, "feed_provider": PROVIDER,
                "message": "No option expiries for this name (may not be in F&O)."}
    chosen = expiry if (expiry and expiry in expiries) else expiries[0]
    try:
        chain = client.option_chain(sid, chosen, seg="NSE_EQ")
    except Exception as e:
        return {"ticker": tk, "configured": True, "available": False, "expiries": expiries,
                "expiry": chosen, "feed_provider": PROVIDER, "message": f"{feed} option-chain error: {_dhan_error(e)}"}
    return {"ticker": tk, "configured": True,
            "available": bool(chain and chain.get("strikes")),
            "expiries": expiries, "expiry": chosen, "feed_provider": PROVIDER, **(chain or {})}
