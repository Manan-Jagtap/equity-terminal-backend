# EQUITY TERMINAL — HANDOFF DOCUMENT

*Last updated 26 August 2026. Read this first, then `ARCHITECTURE.md` (as-built
topology + the valuation pipeline), then `deploy/aws/` (how anything reaches
production). Change history: CHANGES_2026-07.md. Compliance: COMPLIANCE.md.*

> **Infrastructure.** Production is **AWS Mumbai (ap-south-1)**: one EC2 box
> (`i-0f60f2dd6fc5fabd5`) running Docker containers `caddy` → `web` and
> `scheduler`, RDS Postgres 16, ECR for the image, S3 for `/opt/app.env`,
> Cloudflare R2 for documents and encrypted backups. Frontend on Vercel at
> equityverdict.com; API at `https://api.equityverdict.com`. **Railway was
> retired 18 Jul 2026** — ignore every `up.railway.app` URL below and treat
> "Railway variables" as "`/opt/app.env` on the box". `DEPLOY_NOTES.md` is the
> dead Railway runbook, kept for history.

> **⚠ The most recent thing to go wrong: the VENDOR HOST.** IndianAPI's
> Developer plan is served from its own dedicated host,
> **`https://dev.indianapi.in`**. The shared `stock.indianapi.in` does not reach
> this plan — it answers 429 forever while the vendor console shows **0**
> requests used against a full 10,000. We spent nine days in Aug 2026 reading
> that as an exhausted quota, and a month earlier read a 404 from the same wrong
> host as the vendor revoking `/documents` (that was "DATA-12" — a
> misdiagnosis; §3). Both `/documents` and `/historical_stats` are on-plan and
> answering. **If vendor data looks dead, check the HOST before the quota.**

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
| Frontend (React/Vite) | https://equityverdict.com | Vercel, auto-deploys on push to `main`; a merge webhook is occasionally MISSED — retrigger with an empty commit |
| TLS edge | `caddy` container on EC2 | `/opt/Caddyfile`, auto Let's Encrypt, proxies `web:8080` on the `edge` docker network |
| Backend API (FastAPI) | https://api.equityverdict.com | `web` container, same box, 1× uvicorn |
| Scheduler (worker) | `scheduler` container, same box | same image, command `python scheduler.py` |
| Database | AWS RDS Postgres 16 `equity-terminal-db` | **Alembic owns the schema**; `app/migrations_boot.py` stamps-or-upgrades at boot, entrypoint runs `alembic upgrade head` fail-closed |
| Image | ECR `593334122677.dkr.ecr.ap-south-1.amazonaws.com/equity-terminal` | built from `deploy/aws/Dockerfile` (repo-root context) |
| Env | `/opt/app.env` on the box (pulled at boot from the private S3 config bucket) | passed with `docker run --env-file` — **bound at container CREATE** |
| Documents + backups | Cloudflare R2 | quarterly PDFs; weekly Fernet-encrypted DB dumps (`BACKUP_KEY`) |
| Repos | github.com/Manan-Jagtap/{equity-terminal, equity-terminal-backend} | local: ~/equity-terminal, ~/backend |

**Local dev:** backend tests need `./venv313/bin/python` (3.13 venv,
untracked; the system python cannot import the codebase) with a fresh
`DATABASE_URL=sqlite:////tmp/…`. Frontend: `npm run build`; eslint kept at
exact baselines. NEVER `git add -A` in the backend repo — stage explicit paths.
The owner merges PRs on GitHub, so **local `main` lags**: branch off
`origin/main`, and run `git diff origin/main --stat` before committing.

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
ownership, documents, ratios/growth/quarter history (`/historical_stats`),
company news (`recentNews` inside `/stock`; there is no `/company_news`, and
`/news` ignores `stock_name`). **Base `https://dev.indianapi.in`** — the
Developer plan's dedicated host, which also serves the analyst endpoints
(`analyst.indianapi.in` answers us 403). It is both the code default and a line
in `/opt/app.env`, and **the env var wins, so both must say it**;
`deploy/aws/set-vendor-key.sh` writes the key and both base URLs together after
probing the host it is about to write. Budget pre-flight in `app/api_budget.py`
against `INDIANAPI_MONTHLY_BUDGET` (`CALLS_PER_FULL_INGEST = 10`). Profiles are
**snapshot-first**: last good payload persists 7 days in CompanyInsight,
refresh is budget-gated, vendor failure degrades to last-known-good;
`RUN_PROFILE_SNAPSHOTS` re-arms a full backfill.

**The DATA-12 correction — read this before trusting any "the vendor removed
X" note in this repo.** From 24 Jul to 25 Aug 2026 code, docs and one test all
recorded that the vendor had taken `/historical_stats` and `/documents` off our
plan: `{"info": "Not a valid script_code"}` for every name, and a 404 on
`/documents`. It never happened — those were the SHARED host's answers to a
Developer-plan key. On `dev.indianapi.in`, `/historical_stats` returns a real
12-year series and `/documents` returns 200; both `_ON_PLAN` flags in
`app/ingest/indianapi_ingester.py` are back on. Three things from that episode
are real and stay: **BSE's own anti-bot block** on `api.bseindia.com` (unrelated
to IndianAPI), the **error-envelope guard** (a body whose only keys are
error/info/message/detail is not data and must never be written over a stored
value, whatever produced it), and **~633 stored insight rows whose
ratios/growth still hold an `{"info": ...}` body** — real damage, repaired
separately.

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
Ke = Rf (live 10Y) + CALCULATED beta × ERP 5.0% (regression of ~weekly
returns on the NIFTY 50 over ~3y, shrunk to the sector prior by fit R² —
app/beta.py, cached per recompute; sector beta is the fallback) (not full-CRP;
documented in sector_params.py). Net worth = reported, else capital +
reserves — never bare share capital.

**⚠️ PARITY CONTRACTS — three of them, all CI-gated.** Re-run after ANY change
to `engines.py`, `derive.py`, `sector_params.py`, or the recommend gates:
```
cd ~/backend
venv313/bin/python tests/gen_parity_cases.py  ~/equity-terminal/tests/parityCases.json
venv313/bin/python tests/gen_derive_cases.py  ~/equity-terminal/tests/deriveCases.json
venv313/bin/python tests/gen_verdict_cases.py ~/equity-terminal/tests/verdictCases.json
cd ~/equity-terminal
node tests/engineParity.mjs    # must print  60/60   engine.js ↔ engines.py
node tests/deriveParity.mjs    # must print  48/48   derive.js ↔ derive.py
node tests/verdictParity.mjs   # must print 113/113  the verdict ladder
```
A pre-push hook (`scripts/hooks/pre-push`; reinstall after a fresh clone)
regenerates and verifies these, and CI re-checks the committed fixtures
(ARC-05) — so a stale fixture blocks the push rather than reaching production.

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

Mon–Fri 15:45 IST (10:15 UTC): EOD prices → Dhan 30-day top-up (self-heals
gaps) → recompute → verdict/signal snapshots. Sun 06:00 IST: full refresh +
rolling weekly fundamentals cohort. Every 90 min: intraday (market-hours
gated). Daily 20:30 UTC coverage self-heal; Sun 03:00 integrity sweep; Sun
04:00 encrypted backup. The full table is in `ARCHITECTURE.md` §5 — keep it
there, not here.

One-shot flags (set in `/opt/app.env` → **cut over** → REMOVE): RUN_DHAN_REPAIR,
RUN_DHAN_BACKFILL, RUN_FUNDAMENTALS_BACKFILL, RUN_PROFILE_SNAPSHOTS,
RUN_BOOTSTRAP_NOW. A cutover recreates BOTH containers — it kills in-flight
ingest and the boot recompute reruns.

**Missed runs are now caught, because they used to be invisible.** `schedule`
recomputes each job's next_run from PROCESS START, so recreating a container
after a job's slot silently drops that day's run. Two mechanisms:
- `app/eod_coverage.py` — asks the DATA whether the last settled session is in
  `historical_prices`, claims and self-heals it, and grades per-session name
  COUNTS (`eod_names` / `eod_names_prior` on `/api/health`). `max(date)` alone
  cannot see a partial session; that is how the 14 Aug 2026 miss hid for three
  days behind 21 newly listed names.
- `app/job_runs.py` — records each of the 15 scheduled jobs, publishes
  `jobs_overdue` / `jobs_overdue_stuck`, and replays only the jobs whose replay
  recovers something (`catch_up`); `run_full` and `run_results_calendar` are
  recorded but never replayed, on cost.

**Never `import scheduler` to inspect production** — importing that module runs
its whole boot sequence. That is precisely why the two modules above live
outside it.

## 9. DEPLOY (the only supported path)

```bash
cd ~/backend
./deploy/aws/deploy.sh --check      # what is live right now; changes nothing
./deploy/aws/deploy.sh              # pull → build amd64 → inspect image → push ECR → cut over → smoke
```
`deploy.sh` exists because **four deploys were done by hand in Aug 2026 and
three of them shipped nothing** — the build failed or never ran, the cutover
pulled an unchanged `:latest`, and `/api/health` came back green because the OLD
code is perfectly healthy. Every gate in it marks a real failure: Docker daemon
down, missing `-f deploy/aws/Dockerfile`, a stale local checkout, zsh eating
`$ECR:latest`, running the cutover locally instead of over SSM — and the gate
that was missing entirely, LOOKING INSIDE THE IMAGE BEFORE PUSHING.

Vendor key or host change: `./deploy/aws/set-vendor-key.sh` (`--check` /
`--prompt`). It never takes the key on argv, validates it against the live
vendor BEFORE touching `/opt/app.env`, and writes `INDIANAPI_KEY`,
`INDIANAPI_BASE` and `INDIANAPI_ANALYST_BASE` together. `ROLLBACK.md` rolls back
to a previous `git-<sha>` tag; `DR_DRILL.md` rebuilds the box from scratch.

## 10. GATES THAT EXIST NOW

- **CI (`.github/workflows/ci.yml`)** — ruff bug-class lint; pytest on SQLite
  AND on Postgres; committed parity fixtures must match (ARC-05); the valuation
  **calibration gate** against `tests/calib_baseline.json`; boot migrations must
  succeed on Postgres; an alembic **round-trip** (upgrade → downgrade base →
  upgrade); and an **image-boot job** that builds the production image and
  requires `/api/health` → 200.
- **Uptime cron (`uptime.yml`)** — probes prod every 30 min and fails on
  `errors_1h` > 25, `error_hours_24h` ≥ 12, `scheduler_beat_min` > 120,
  `price_age_days` > 3, red/stale `integrity`, plus a real CONTENT path
  (`/api/companies`) because a 200 shell is not data.
- **Branch protection on `main`** (added 26 Aug 2026) — `backend-tests`,
  `backend-tests-postgres` and `backend-image-boots` must pass before a PR
  merges. `enforce_admins` is OFF deliberately: during the GitHub Actions
  outage of 26 Aug 2026 required checks would otherwise have blocked every
  merge with no way out, so the owner keeps an explicit override. `strict` is
  also OFF — a PR need not be rebased every time `main` moves, which for a
  single-maintainer repo costs more than it buys. Frontend `main` is NOT
  protected.
- **Pre-push hook** — regenerates and verifies the three parity harnesses.
- **Accuracy gates in the app** (§5) — metrics bands, statement identities,
  ratio cells, price V-spikes. A wrong number is never preferred to no number.
- **Vendor honesty** — `app/vendor_meter.py` publishes `vendor_ok`,
  `vendor_fail`, `vendor_last_ok_min` on `/api/health`, because on 14 Aug 2026
  every vendor call failed for 5+ hours while health read `ok, errors_1h: 0`
  (caches served, no exception raised).

## 11. KNOWN OPEN

- **~633 insight rows hold an `{"info": ...}` body in ratios/growth** — the real
  damage from the DATA-12 window, repaired separately. The envelope guard stops
  new ones.
- **`INDIANAPI_MONTHLY_BUDGET` in `/opt/app.env` may still read 40000**, a
  Growth-plan number, while the live plan is Developer (~10k). Confirm it before
  any full-universe refresh; the pre-flight only protects against the number it
  is given.
- ~160 names land in MANUFACTURING from a null vendor sector — a data backfill
  need, not a rules bug. `POST /api/admin/run-backfill` (admin auth,
  budget-guarded, 2–3 passes) from the prod terminal's console.
- YESBANK vendor statement gap; TVSMOTOR/JSL override queue.
- Div-yield strip absent where the vendor publishes none (honest gap).
- Options full 208-name sweep pending a market session on the auto-token.
- Block deals / FII-DII named holders: IndianAPI does NOT carry them — needs
  NSE/BSE official archives + a licensing review first.
- Not safe to charge users yet (COMPLIANCE.md: SEBI RA registration +
  data-redistribution licensing).

## 12. TRAPS THAT HAVE ACTUALLY BITTEN

1. **A deploy after 15:45 IST silently skips that day's EOD run.** `schedule`
   recomputes next_run from process start. Cost: the 14 Aug 2026 session, unseen
   for three days. Mitigated by `eod_coverage.py` + `job_runs.py`, not removed —
   prefer deploying outside the scheduled slots, and check `jobs_overdue`.
2. **Env is bound at container CREATE.** Editing `/opt/app.env` and running
   `docker restart` ships NOTHING. Only a recreate (`cutover.sh`, which
   `deploy.sh` and `set-vendor-key.sh` both call) picks up a changed env.
3. **A build can fail while the deploy reports success.** `/api/health` is green
   on old code. Compare the ECR `:latest` digest with the running container's —
   `deploy.sh --check` does exactly this.
4. **Placeholders substitute cleanly.** `PASTE_THE_KEY_THAT_RETURNED_200` and
   `NEW_KEY_HERE` were both written into production as if they were keys; the
   file wrote, the container recreated, health stayed 200, and only the vendor
   knew. Anything that writes a secret must ASK THE VENDOR FIRST.
5. **An error body is evidence about the request, not proof about the plan.**
   DATA-12: a 404 and an `{"info": ...}` body from the wrong host were read as
   the vendor withdrawing endpoints, and the code stopped calling them for a
   month. Check the host, the key and the plan before concluding a vendor
   changed its mind.
6. **Absent CI runs look exactly like a broken workflow.** On 26 Aug 2026 a
   GitHub Actions incident (15:11 UTC) queued runs ~20 min and dropped others;
   it read as a misconfigured repo for a while, and the frontend/backend
   difference was pure queue luck. **Check githubstatus.com before diagnosing
   the config.** `gh workflow run CI --ref main` now answers "is main green?"
   directly — that trigger was added because during the outage there was no way
   to ask.
7. **A PR can be merged faster than CI can start.** #159 was opened and merged
   28 seconds apart, so nothing had run. Branch protection (§10) is now the
   backstop; before it, only discipline was.
8. **Never `import scheduler` to check production** — it runs the boot sequence.
9. **Local `main` lags** (the owner merges on GitHub) and **Vercel occasionally
   misses a merge webhook** — branch off `origin/main`; retrigger Vercel with an
   empty commit.
