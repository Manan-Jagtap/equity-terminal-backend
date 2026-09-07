"""
app/market_data.py — the single seam every market-data caller imports.

    from app.market_data import client, instruments

`MARKET_DATA_PROVIDER` selects the backing vendor:
    upstox  (default) — app/upstox/*, a read-only 1-year Analytics token
    dhan              — app/dhan/*, the previous vendor (24h TOTP-minted token)

Both modules expose the SAME function names, argument order and return shapes,
so switching vendors is an env change plus a container restart — not a code
change. The Dhan modules are deliberately left in the tree as a rollback path:
if the Upstox token is revoked or its terms change, set MARKET_DATA_PROVIDER=dhan
(with the DHAN_* credentials restored) and redeploy.

WHY THIS EXISTS: the Dhan Data API subscription (₹499/mo) lapsed and its token
mint began returning {"message":"Invalid TOTP"} as an HTTP *200*, which
app/dhan/auth.py's `except Exception: pass` swallowed into "not configured" —
so live prices, option chains and index history went dark silently. The
provider is now explicit and its health is surfaced (see /api/health).

The vendor is resolved at import time, so a change takes effect on restart.
"""
from __future__ import annotations
import os

PROVIDER = os.getenv("MARKET_DATA_PROVIDER", "upstox").strip().lower()

if PROVIDER == "dhan":
    from .dhan import client, instruments          # noqa: F401
else:
    PROVIDER = "upstox"
    from .upstox import client, instruments        # noqa: F401


def provider_status() -> dict:
    """Vendor identity + reachability, for /api/health and the admin panel.
    Deliberately does NOT swallow the reason a vendor is unusable — the whole
    point of this indirection is that a dead feed is visible, not silent."""
    return {
        "provider": PROVIDER,
        "configured": client.configured(),
        "instruments": instruments.coverage(),
    }
