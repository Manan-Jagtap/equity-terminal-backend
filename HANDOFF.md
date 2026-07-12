# EQUITY TERMINAL — HANDOFF DOCUMENT
*Last updated 12 July 2026. Read top-to-bottom before touching anything.
Detailed change history: CHANGES_2026-07-04.md (addenda 1–19). Compliance: COMPLIANCE.md.*

---

## 1. WHAT THIS IS

An independent equity research terminal for Indian markets covering the
**full Nifty 500** (501 visible names). Differentiators: a **transparent,
independent valuation engine** (every assumption traceable), a **public
track record** (the model grades its own calls daily, nothing backfilled),
and a **100%-accurate-or-absent doctrine** — plausibility/integrity gates on
every published metric, statement line, ratio and date; a wrong number is
never preferred over no number.

**Owner:** Manan Jagtap (system mananjagtap2000@gmail.com; terminal prod
admin mananjagtap27@gmail.com; Railway CLI mananjagtap2703@gmail.com).

## 2. LIVE SYSTEM MAP

| Piece | Where | Notes |
|---|---|---|
| Frontend (React/Vite) | https://equity-terminal-one.vercel.app | Vercel, auto-deploys on push to `main` |
| Backend API (FastAPI) | https://equity-terminal-backend-production.up.railway.app | Railway `equity-terminal-backend` |
| Scheduler (worker) | Railway `equity-terminal-scheduler` | same repo, `python scheduler.py` |
| Database | Railway Postgres (shared) | additive column migrations run in `app/main.py` |
| Repos | github.com/Manan-Jagtap/{equity-terminal, equity-terminal-backend} | local: ~/equity-terminal, ~/Downloads/backend |

**Local dev:** backend tests need `./venv313/bin/python` (3.13 venv,
untracked; system 3.9 cannot import the codebase) with a fresh
`DATABASE_URL=sqlite:////tmp/…`. Frontend: `npm run build`; eslint kept at
exact baselines. `.python-version` pin = Railway build requirement.
NEVER `git add -A` in the backend repo.

## 3. DATA VENDORS (two, complementary, cross-checked)

**Dhan — everything price-shaped:** live LTP batch (500 equities + 11 NSE
indices, 12s cache, `app/live_prices.py`), 5-yr OHLCV history (vendor
split-adjusted — never re-apply the CorporateAction ledger to price series),
option chains, index history. Tokens **self-mint via TOTP**
(`app/dhan/auth.py`: official generateAccessToken, RFC-6238 stdlib TOTP,
one token shared across services via `kv_store`, renewed 30 min before the
24h expiry, 10-min backoff after failed mints, 401 self-heal in
`client._post`). Credentials ONLY in Railway env: `DHAN_CLIENT_ID`,
`DHAN_PIN`, `DHAN_TOTP_SECRET` — the **API-specific** TOTP from web.dhan.co
(app-login 2FA secrets are rejected with "Unauthorized Request"). Data
endpoints only; trading APIs are never called. Owner diagnostic:
`/api/admin/dhan-totp` compares the server-derived code with the
authenticator.

**IndianAPI — everything fundamentals-shaped:** statements, profiles,
ownership, docs, company news (`recentNews` inside `/stock`; the production
host has no `/company_news` and `/news` ignores `stock_name`). Growth plan,
`INDIANAPI_MONTHLY_BUDGET=40000`, budget pre-flight in `app/api_budget.py`.
Base `https://stock.indianapi.in` (the dev host rejects the key). Profiles
are **snapshot-first**: last good payload persists 7 days in
CompanyInsight, refresh is budget-gated, vendor failure degrades to
last-known-good; `RUN_PROFILE_SNAPSHOTS` re-arms a full backfill.

**Failover:** health-based (not presence-based) in the scheduler — zero
Dhan rows ⇒ IndianAPI price fallback; wider-tier EOD escalation; the daily
cross-check (`app/quality_routes.py`) surfaces divergence + token expiry on
the dashboard.

## 4. VALUATION ENGINE (crown jewels — change with extreme care)

Files: `app/engines.py` (models), `derive.py` (assumption derivation),
`sector_params.py` (sector table + TICKER_OVERRIDES), `assemble.py`,
`data_quality.py`. Frontend mirror `src/lib/engine.js`; recommend() guards
mirror in `src/lib/recommend.js`.

Methodology (v2 "CAP engine"): non-financials two-stage FCFF DCF (stage-1
flat N/2, linear fade to terminal, terminal reinvestment = g_t / mature
ROIC); financials residual income with the same two-stage ROE design.
Dynamic fade horizon in `derive.py` (8/11/14y non-fin, 8/10/12y fin, by
ROIC/growth/ROE quality; METAL/ENERGY never extended; AUTO/CEMENT stage-1
capped 12%, METAL/ENERGY 8%, others 18%). Blend: non-fin DCF 55% + exit
EV/EBITDA 30% + sector P/E 15%; fin RI 65% + Gordon P/B 20% + P/E 15%.
Verdict gates via `data_quality`: BUY needs composite ≥ 68, MoS > 15%, HIGH
confidence; MoS > 2.0 ⇒ implausible ⇒ LOW CONF ⇒ shown as **NO CALL**.
Ke = Rf 6.9% + sector beta × ERP 5.0% (deliberately not full-CRP;
documented in sector_params.py). Net worth = reported, else capital +
reserves — never bare share capital.

**⚠️ PARITY CONTRACT** after ANY engine/sector change:
```
cd ~/Downloads/backend && python3 tests/gen_parity_cases.py ~/equity-terminal/tests/parityCases.json
cd ~/equity-terminal && node tests/engineParity.mjs   # must print 60/60
```

## 5. ACCURACY GATES (the platform's spine)

- **Metrics** `app/metrics.py`: every metric carries a plausibility `band`;
  out-of-band ⇒ absent. No fabricated metrics.
- **Statements** `app/financials.py::_sanitize_statements`: annual columns
  must satisfy accounting identities (total income ≥ PBT, lender top line ≥
  PBT, no >80% YoY interest collapse, implied CoF ≥ 1% on a real borrowing
  book); misfiled quarterly lines drop; cost lines display as magnitudes.
- **Ratios** `app/history_routes.py::_gate_ratio_series`: per-cell bands.
- **Prices** `app/price_hygiene.py::drop_bad_ticks`: V-spike filter.
- **Dates** `app/documents_routes.py`: rating years recovered from agency
  URLs; frontend renders feed-true granularity ("May 2026", never "1 May").
- **Universe-only:** market movers/52-week lists map vendor rows to covered
  tickers (nseCode → RIC symbol → word-bounded name match) and drop
  outsiders; peer tables list covered names only.

## 6. FEATURE MAP (frontend `src/components/`)

Dashboard (universe-only movers, clickable value-bearing indices → charts,
data-health strip) · Screener (saved screens, query filters) · Ideas ·
Watchlist · Compare · Results · Ownership (all vendor categories + full
trend) · Operations · Sectors (full-universe, expandable per-sector stock
drilldown) · Portfolio (typed/paste/CSV import with buy dates, LT/ST terms,
analysis panel) · **Fund Manager** (PM note + conviction-scored, rupee-sized
action queue vs inverse-vol targets + momentum + LTCG timing — educational)
· Track Record. Company page: 14 tabs; Options F&O-gated; News tab hidden at
zero items; ⌘K palette ("TCS DCF at 12% growth"); shareable scenarios;
Excel/one-pager exports.

## 7. AUTH / SECURITY / COMPLIANCE

PBKDF2 260k; HMAC bearer tokens (30d); compulsory login; signup = name +
privacy consent + MX-validated email (`app/email_check.py`); AuthEvent
ledger; DPDP account deletion; per-IP rate limits; CORS via
FRONTEND_ORIGIN; `ADMIN_EMAILS` gates /api/admin/* (coverage, users,
auth-events, dhan-totp). **⚠️ A GitHub fine-grained PAT was pasted into
chat sessions in June 2026 — REVOKE it if not already done.**
**Before charging users:** SEBI RA registration + data-redistribution
licensing (COMPLIANCE.md). Fund Manager output is educational decision
support, never advice.

## 8. SCHEDULER (cadences + one-shot flags)

Mon–Fri 15:45 IST: EOD prices → Dhan 30-day top-up (self-heals gaps) →
recompute → verdict/signal snapshots. Sun 06:00 IST: full refresh + rolling
weekly fundamentals cohort (ISO-week slice of VISIBLE_UNIVERSE−UNIVERSE).
Every 90 min: intraday (market-hours gated). One-shot Railway flags
(set → deploy → REMOVE): RUN_DHAN_REPAIR, RUN_DHAN_BACKFILL,
RUN_FUNDAMENTALS_BACKFILL, RUN_PROFILE_SNAPSHOTS, RUN_BOOTSTRAP_NOW.
Pushing the backend repo redeploys BOTH services (kills in-flight ingest;
boot recompute reruns).

## 9. KNOWN REMAINDERS

- YESBANK vendor statement gap (coverage 499/500); TVSMOTOR/JSL override
  queue documented.
- 5-yr journey chart fallback (Business tab, curated names only) — cosmetic.
- Div-yield strip absent where the vendor publishes none (honest gap).
- Options full 208-name sweep pending a market session on the auto-token.
- Block deals / FII-DII named holders: IndianAPI does NOT carry them —
  needs NSE/BSE official archives + licensing review first.
