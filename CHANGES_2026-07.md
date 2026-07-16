# Equity Terminal — Session Changes & Deploy Runbook (2 July 2026)

Four roadmap workstreams, built locally and **fully verified**: backend **96 tests pass**,
engine **parity 60/60**, frontend **build clean**. Phase A is committed on both repos;
B/C/D are on disk, ready to commit (a sandbox couldn't write git lock files — see Deploy §1).

---

## What shipped

### A — Total returns: dividends + corporate actions  *(Critical + High)*
- New `CorporateAction` table + ingester parsing of dividends/splits/bonuses from IndianAPI `stockCorporateActionData`.
- `app/corporate_actions.py` — pure split/bonus price **back-adjustment** + dividend **total-return** math (21 tests).
- Price-history chart is back-adjusted on read (no more fake split cliffs).
- **Track Record**: per-call `total_ret`/`div_ret`, `avg_total_return` cohorts, and a tracked-universe total-return **benchmark**.
- **Portfolio**: dividend income + total P&L (capital + dividends).
- Frontend: Track Record "Total" column + "BUY vs universe" card; Portfolio "Dividends"/"Total return" cards + "Div" column.
- The trust-layer split *heuristic* was **deliberately kept** — it guards stale shares-outstanding vs the vendor's already-adjusted live price, a *different* failure than the price-history adjustment. Deleting it would re-expose the Bajaj Finance P/B-0.7x bug.

### B — Onboarding hardening + quota guardrail
- `ingest_company` now writes the real name/sector/template/type from `/stock companyProfile` for freshly onboarded names — fixes the silent bug where new non-financials were valued on a generic *manufacturing* multiple (a confident-but-wrong BUY risk). Logs a warning when a sector can't be classified.
- Scheduler auto-create uses **non-null placeholders** (`sector="Unknown"`, `shares=0.0`) so onboarding can't fail Postgres NOT NULL.
- Durable monthly **IndianAPI budget**: `models.ApiUsage` + an in-process `_get` counter + a `run()` **pre-flight abort** against `INDIANAPI_MONTHLY_BUDGET` (override with `INDIANAPI_ALLOW_OVER=1`).

### C — Universe → Nifty 100 (vetted tranche)
- **Reframe**: ~500 names were already ingested + valued; the frontend simply *hid* all but the Nifty 50. So this was a **visibility + classification** job, not an ingest.
- Added **REALTY** + **CHEMICALS** valuation sectors, mirrored bit-for-bit in `engine.js` (parity re-checked **60/60**). Cut MANUFACTURING-fallbacks **135 → 98**; the rest (capital goods, textiles, construction) are manufacturing-shaped and err *conservative*.
- **Single-source visibility**: new `/api/universe` endpoint (Nifty 100) — the frontend now reads its whitelist from it, so backend/frontend can no longer drift (the hardcoded set is a fallback only).
- Cadence: daily EOD prices + recompute extended to **Nifty 100**; intraday + weekly-full stay Nifty 50 (quota-safe).
- **INDIGO held back** — an airline has no defensible sector model yet; better hidden than mis-priced.

### D — Insurer P/EV + conglomerate SOTP
- `app/alt_models.py` — backend **Sum-of-the-Parts** (RELIANCE, ADANIENT) + life-insurer **P/EV appraisal** (SBILIFE, HDFCLIFE, ICICIPRULI, LICI).
- Wired into `recommend()` **outside** the parity-tested engine core → `engine.js` and the 60/60 contract are untouched.
- These names flip from "LOW CONF by design" to real **MEDIUM-confidence** verdicts, with `method` = Sum-of-the-Parts / P/EV Appraisal. Verified on real data (RELIANCE → SOTP HOLD; SBILIFE → P/EV appraisal).

---

## Insurer embedded values (FY26 — now real, not placeholders)
`INSURER_EV` in `app/alt_models.py` was updated from placeholders to disclosed
**FY26 (year ended 31 Mar 2026)** figures:

| Insurer | Indian EV | EV/share | RoEV | Justified P/EV | Model intrinsic |
|---|---|---|---|--:|--:|
| SBILIFE | ₹80,790 cr | ₹805.40 (disclosed) | 19.7% | 2.41x | ₹1,938 |
| HDFCLIFE | ₹62,139 cr | ₹288.8 (÷~215.2cr sh) | 15.0% (op) | 1.61x | ₹465 |
| ICICIPRULI | ₹52,989 cr | ₹366.7 (÷~144.5cr sh) | 11.9% | 1.08x | ₹398 |
| LICI | ₹7,89,185 cr | — | — | — | **LOW CONF (omitted)** |

**LIC is deliberately not modelled.** Its reported EV massively overstates
distributable shareholder value (90:10 participating-surplus structure) and a
FY26 bonus issue muddies per-share figures, so a naive P/EV would mislead — LIC
stays LOW CONF pending a bespoke shareholder-vs-policyholder appraisal.

Re-check these when the insurers next report (EV is a point-in-time actuarial
number). Sources: SBI Life FY26 performance release (sbilife.co.in); HDFC Life
FY26 results (outlookbusiness.com / earnings call); ICICI Pru Life FY26
(moneymuscle.in / iciciprulife.com); LIC FY26 EV (business-standard.com).

## ⚠️ Verify before relying (illustrative inputs)
1. **SOTP segment EVs** — `SOTP_PRESETS` in `app/alt_models.py` are illustrative and duplicated in `src/components/SegmentSOTP.jsx` (two copies — keep in sync).
2. **Nifty Next 50 membership** — `NIFTY_NEXT_50` in `app/ingest/indianapi_ingester.py` is a best-effort snapshot; the index rebalances. Edit that one set to change what's visible.
3. **DCF-tab sliders** for insurers still run Residual Income (engine.js); the base-case verdict is P/EV from the backend. Cosmetic mismatch only.

---

## Deploy runbook

### 1. Commit (from your Mac — the sandbox couldn't write git locks)
Clear any stale lock first, then commit B/C/D (Phase A is already committed: backend `976cc7f`, frontend `a8db976`).
```bash
cd ~/Downloads/backend && rm -f .git/index.lock
git add app/engines.py app/ingest/compute_valuations.py app/ingest/indianapi_ingester.py \
        app/main.py app/models.py app/sector_params.py scheduler.py \
        app/alt_models.py app/api_budget.py \
        tests/test_alt_models.py tests/test_api_budget.py tests/test_onboarding.py
git commit -m "Onboarding + quota guardrail, Nifty 100 visibility, REALTY/CHEMICALS, SOTP + insurer P/EV"

cd ~/equity-terminal && rm -f .git/index.lock
git add src/App.jsx src/lib/engine.js tests/parityCases.json
git commit -m "REALTY/CHEMICALS sectors (parity-mirrored) + single-source Nifty 100 visibility"
```

### 2. Pre-push verification (all must pass)
```bash
cd ~/Downloads/backend && DATABASE_URL=sqlite:////tmp/_pytest_terminal.db python3 -m pytest tests/ -q   # 96 passed
python3 -m compileall -q app scheduler.py -x '\.bak|__pycache__'
cd ~/equity-terminal && npm run build && node tests/engineParity.mjs                                     # 60/60
```

### 3. Push (needs a FRESH GitHub token — the old PAT should be revoked)
```bash
git push https://x-access-token:<TOKEN>@github.com/Manan-Jagtap/equity-terminal-backend.git HEAD:main
git push https://x-access-token:<TOKEN>@github.com/Manan-Jagtap/equity-terminal.git HEAD:main
```
Backend push → Railway redeploys both services; the scheduler boot **auto-recomputes all valuations** (REALTY/CHEMICALS + SOTP/P-EV go live) and re-onboards any universe gaps. Frontend push → Vercel deploys.

### 4. Post-deploy: backfill corporate actions (one-off)
Dividend/split rows only populate on an ingest that runs the new parser. To backfill without waiting for Sunday's full refresh: on the **scheduler** service set `RUN_FULL_NOW=true` (or `RUN_REINGEST_TICKERS=A,B,C`), redeploy, then **remove** the flag. Watch the budget pre-flight line in the logs.

### 5. Optional env
- `INDIANAPI_MONTHLY_BUDGET` (default `10000`) — quota-guard ceiling. Nifty 100 EOD adds ~+1,100 calls/mo; if you later push Next-50 into weekly-full or intraday, raise this or the plan.

### 6. Live checks (unique `cb=` every time)
```
GET /api/health?cb=<n>
GET /api/universe?cb=<n>       → 101 tickers, tier "nifty100"
GET /api/companies?cb=<n>      → RELIANCE/ADANIENT method "Sum-of-the-Parts"; SBILIFE method "P/EV Appraisal"
GET /api/backtest?cb=<n>       → cohorts carry avg_total_return; "benchmark" block present
```

---

## Files changed this session

**Backend** (`~/Downloads/backend`)
- Phase A *(committed 976cc7f)*: `app/corporate_actions.py`, `models.py`, `history_routes.py`, `backtest.py`, `backtest_routes.py`, `portfolio_routes.py`, `ingest/indianapi_ingester.py`, `tests/test_corporate_actions.py`, `tests/test_backtest.py`, `tests/test_portfolio.py`
- Phase B/C/D *(to commit)*: `app/alt_models.py`, `app/api_budget.py`, `app/engines.py`, `app/main.py`, `app/models.py`, `app/sector_params.py`, `app/ingest/compute_valuations.py`, `app/ingest/indianapi_ingester.py`, `scheduler.py`, `tests/test_alt_models.py`, `tests/test_api_budget.py`, `tests/test_onboarding.py`

**Frontend** (`~/equity-terminal`)
- Phase A *(committed a8db976)*: `src/components/TrackRecord.jsx`, `src/components/Portfolio.jsx`
- Phase C *(to commit)*: `src/App.jsx`, `src/lib/engine.js`, `tests/parityCases.json`

---

# Addendum — 16 July 2026 (roadmap sprint)

Six roadmap workstreams shipped + a full classification audit, all live-verified
on production. Engine **parity 60/60**, backend compiles, frontend build clean.

## Shipped
- **Sentiment scoring** (`app/sentiment.py`) — transparent 0–100 from concall
  tone + estimate revision + beat/miss streak; each leg's contribution returned.
  Wired into `/api/companies/{ticker}` (guarded `_safe_sentiment`) and the
  screener list (`sentiment_by`). Frontend: Verdict-tab MARKET SENTIMENT card +
  sortable screener column. Live: 616/1001 names scored.
- **Baskets** (`app/baskets.py`, `/api/baskets`) — 6 smart-beta factor baskets
  (Value/Quality/Momentum/Low-Vol/Growth/QARP) + 8 thematic sector-rule baskets,
  over `ranked_visible`. New **Baskets** frontend page (nav + card grid). The
  MANUFACTURING residual is excluded from the Materials theme.
- **Strategy backtester** (`app/strategy_backtest.py`, `/api/strategy/*`) —
  point-in-time price-strategy sim (momentum, low-vol, trend, 52w, mean-reversion)
  over 5-yr history vs NIFTY 50, no look-ahead, **survivorship-disclosed**,
  curve anchored at first investable rebalance. Frontend: **Strategy Lab** tab in
  Baskets (rule builder + equity curve + metrics + holdings).
- **Engine parity re-sync** — `src/lib/engine.js` re-ported to `app/engines.py`
  bit-for-bit (25-sector params, RI 0.6·N + NPA haircut, FCFF WACC floor + margin
  glide, forward multiples, DDM 4th component + [0.5,2.2]× band cap). Parity
  **0/60 → 60/60**. `valuation.js` remains a separate fallback engine, documented
  as NOT under the contract.
- **Options strategy builder** — new Strategy Builder tab in `OptionsTab.jsx`:
  multi-leg payoff-at-expiry (8 presets), breakevens, max P&L, priced off the
  live chain LTPs. Payoff math unit-verified.
- **Portfolio vs-benchmark** — `benchmark_block` computes capital-matched NIFTY 50
  alpha (same rupees, same buy dates); "Alpha vs NIFTY" summary card.

## Classification & valuation
- **Full 1001-name classification audit.** Rules verified sound for the ~830
  names with a vendor sector. 6 vendor sectors newly mapped (`Personal &
  Household Prods.`→CONSUMER, Tires→AUTO, Footwear/Appliance/Printing→
  CONSUMER_DISC, Oil Well→ENERGY). Self-test 13/13.
- **~170 names remain in MANUFACTURING because they are un-ingested stubs**
  (vendor sector "Unknown", ticker-case name, 0 statements). NOT a rules bug —
  needs the fundamentals backfill (`POST /api/admin/run-backfill`) to onboard
  them from IndianAPI. See HANDOFF §"pending".
- **SOTP extended** to L&T, ITC, Grasim, Vedanta, Bajaj Holdings, Godrej
  Industries (illustrative segment EVs, MEDIUM confidence). All live as
  `method=Sum-of-the-Parts`.

## Addendum 2 — 16 July 2026 (feature backlog cleared)
- **#92 Editable 3-statement model** — new "Model" tab (`ThreeStatementModel.jsx`):
  driver-based linked P&L → CF → net-debt roll + transparent FCFF DCF, separate
  from the parity engine. Math unit-verified. Lenders get an RI redirect.
- **#93 Options strategy builder** — shipped (payoff/breakevens/max-P&L, 8 presets).
- **#96 Trading-terminal chart** — verified already complete (full indicator suite
  on lightweight-charts: SMA/EMA/Bollinger/VWAP + RSI/MACD/Stochastic/ATR, volume,
  drawing tools, live candle, fair-value line). Marked done.
- **#95 Portfolio** — vs-NIFTY capital-matched benchmark shipped; FIFO tax-lots +
  digests remain.

Remaining: #95 tax-lots/digests · news-headline sentiment leg · real SOTP segment
financials · collapse the valuation.js fallback engine · more SOTP conglomerates ·
the ~170-name IndianAPI backfill (owner) + key rotations (owner).
