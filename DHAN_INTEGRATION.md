# Dhan Integration — Setup & Deploy Runbook (July 2026)

Adds Dhan's Data API as a **REST-only** second source. It unlocks price history
(→ real 5-yr charts + longer factor windows), Nifty-500-scale prices, and an
**Options** tab (chain, OI, IV, greeks, PCR). Built to **not disturb a separate
recorder**: the terminal opens **zero WebSocket connections**.

## What shipped
- `app/dhan/client.py` — REST client: `/charts/historical`, `/optionchain`, `/optionchain/expirylist`. Self-rate-limited (≤5 req/s data, 1 req/3s option chain). Returns `None` when unconfigured.
- `app/dhan/instruments.py` — fetches the public scrip-master CSV → `{NSE ticker: securityId}` (equities + indices), cached ~daily.
- `app/dhan/backfill.py` — daily OHLCV → `HistoricalPrice` (idempotent). Fills the currently-empty table.
- `app/dhan_routes.py` — `GET /api/dhan/status`, `GET /api/companies/{ticker}/options[?expiry=]`.
- Scheduler one-off flag `RUN_DHAN_BACKFILL`.
- Frontend **Options** tab (chain + PCR + ATM highlight).
- 6 unit tests (client normalization + instrument parsing). 126 backend tests total; engine parity 60/60.

## Env vars (Railway — backend service)
| Var | Needed for | Notes |
|---|---|---|
| `DHAN_ACCESS_TOKEN` | everything | JWT, **rotates ~daily ~09:00**. Refresh out-of-band (copy from the recorder's SSM/S3, or a small daily job). REST-only means a stale token just fails the next call — no data loss. |
| `DHAN_CLIENT_ID` | option chain | Your Dhan client id (the option-chain endpoint requires it). |

Everything degrades gracefully when these are unset (the Options tab shows "requires Dhan", backfill logs "not configured").

## Protecting the recorder (the three catches, handled)
1. **Token dies daily** → the terminal only *reads* market data; a stale token fails softly. Point it at the **same** token the recorder publishes (SSM/S3) so there's one refresh, not two.
2. **WebSocket cap (5, recorder uses 3)** → the terminal uses **0** WebSocket connections (REST-only). Your recorder's feeds are untouched.
3. **REST 5 req/s shared** → the terminal self-limits to ~4.5 req/s and its bulk work is a one-off backfill + occasional option-chain reads. Run the backfill at a quiet time; steady-state is tiny.

## Go-live steps
1. Set `DHAN_ACCESS_TOKEN` (+ `DHAN_CLIENT_ID`) on the backend service → redeploy.
2. **Verify wiring:** `GET /api/dhan/status` → `{"configured": true, "instruments": {"equities": >2000, ...}}`.
3. **Verify options:** `GET /api/companies/RELIANCE/options` → expiries + a chain with OI/IV. Open a name → **Options** tab.
4. **Backfill price history (one-off):** on the **scheduler** service set `RUN_DHAN_BACKFILL=true` (optionally `RUN_DHAN_TICKERS=RELIANCE,TCS` to test a few first), redeploy, watch the log for `Dhan backfill result: {...}`, then **remove the flag**. Charts then show real 5-yr history; the `/history` endpoint already back-adjusts splits/bonuses on read.

## Deferred until Dhan is verified working in prod (don't do blind)
- **Nifty 500 visibility flip** — once the backfill proves prices flow for the broad set, expand `VISIBLE_UNIVERSE` (`indianapi_ingester.py`) in tranches. The IndianAPI quota wall is gone for prices (Dhan is 100k/day), but keep the classification gate (Phase B) so weak names stay LOW CONF.
- **Second-source cross-check** — compare Dhan prev-close vs the stored price to flag stale/divergent data. Small follow-on; wire after status/backfill are confirmed.
- **Point the factor engine at HistoricalPrice** — momentum/low-vol currently use the 1-yr PricePoint (fine for the 126-day windows); switch to the Dhan 5-yr series to enable longer-horizon factors.

## Instrument-mapping caveat
The scrip-master CSV is parsed by header name with fallbacks, but its exact column
values (segment/instrument codes) couldn't be verified offline. If `/api/dhan/status`
shows a low equity count after the token is set, paste the CSV header row and I'll
pin the column matching.
