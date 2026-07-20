# Agents 5–8 — Reliability/Ops · Scale/Cost · Testing/CI · Instrumentation

Read-only audit, 2026-07-20. Backend `/Users/manan_jagtap/Downloads/backend` (branch redesign/phase-0, clean), frontend `/Users/manan_jagtap/equity-terminal`. Live probes (3 calls, gentle): `/api/health` → `{"status":"ok","errors_1h":0,"scheduler_beat_min":4,"price_age_days":3}` (normal for a Monday pre-refresh); `/api/companies?nifty50=true` warm 0.048s/40KB; `/api/universe` → tier **top1000, 1002 names**.

Context: the July 2026 audit (`AUDIT_2026-07/`) already found PERF-01..09. Verified fixed since: PERF-01 (shared `ranked_visible` TTL cache + double-checked lock, `app/signals.py:145-174`), PERF-02 partially (heartbeat + health fields exist), PERF-03 partially (daily backups, KEEP=30, failure recorded to error_log — `scheduler.py:638-667`, `app/backup.py:37`), PERF-04 (middleware counts returned 5xx — `app/main.py:243-258`), PERF-06 (peer_universe batched — `app/main.py:685-691`), PERF-07 (results/ownership 5-min caches). Still open: PERF-05, PERF-08. Findings below are the current gaps.

---

## Lane A — Reliability / Ops

### [OPS-01] Health carries scheduler/staleness signals but nothing alerts on them — silent data rot is still look-only
- Domain: Reliability/Observability / Severity: **S2** / Likelihood: Med-High / Effort: XS / Priority: **P0**
- Location: `.github/workflows/uptime.yml:28-38`; `app/main.py:291-326`; `scheduler.py:1015-1051`
- Evidence: `/api/health` now returns `scheduler_beat_min` and `price_age_days` (the PERF-02 fix), and the docstring at `main.py:294` claims "the uptime workflow alerts when any spikes" — but `uptime.yml` thresholds **only `errors_1h > 25`**. Neither freshness field is checked anywhere. Scheduler job failures only `log.error` to container stdout (nobody reads it); only the backup job routes failure into `errors_1h` (`scheduler.py:649-661`).
- Root cause: PERF-02's remediation shipped the *signal* (heartbeat, health fields) but not the *threshold* step of its own verification plan.
- Why it matters: the most likely failure for this product is a dead/hung scheduler or dead price pipeline — prices, valuations, and the un-backfillable daily Verdict/Alpha/Consensus ledgers (the moat) freeze while health stays green and GitHub emails nothing. Exactly the failure class the field was built for.
- Recommended fix: in `uptime.yml`, after the errors check, fail when `scheduler_beat_min > 240` or `price_age_days > 5` (generous: the `schedule` loop is single-threaded, so a long Sunday full-refresh legitimately blocks the heartbeat for an hour+ — or move `_heartbeat()` to a daemon thread so the threshold can tighten). Optionally route every scheduler job's `except` through `error_log.record_error` like the backup job already does.
- Verification: stop the scheduler container in a rehearsal (docker-compose topology, `deploy/aws/docker-compose.yml`); within one 30-min cron interval the workflow must fail and email.

### [OPS-02] Production is not rebuildable from the repo — Caddyfile, env, deploy command, and user-data all live off-repo
- Domain: Reliability (DR / bus factor) / Severity: **S2** / Likelihood: Low-Med / Effort: S / Priority: **P1**
- Location: `deploy/aws/` (contains only Dockerfile, compose, MIGRATION_AWS.md); `deploy/aws/MIGRATION_AWS.md:162-164, 174-176`; `AUDIT_2026-07/SYSTEM_MAP.md:17`
- Evidence: the as-built topology depends on: `/opt/Caddyfile` (documented only prose-style in ARCHITECTURE.md:37), `/opt/app.env` (pulled at boot from `s3://equity-terminal-config-…/ec2.env` — single copy, versioning status unknown), `~/.equity-terminal/cutover-cmd.json` (the entire deploy procedure, on the owner's laptop only), and EC2 user-data that MIGRATION_AWS.md itself warns "predates the Caddy topology — update it before replacing the instance" (no user-data file exists in the repo at all). One EC2 (`i-0f60f2dd6fc5fabd5`) runs caddy+web+scheduler; there is no consolidated incident runbook (DEPLOY_NOTES.md is marked SUPERSEDED and points back at the migration doc).
- Root cause: the 17-18 Jul migration was executed by hand and the resulting artifacts were never round-tripped into version control.
- Why it matters: if the instance dies (AZ event, EBS loss, fat-fingered termination), recovery is archaeology under pressure, by a self-described low-technical-depth operator. RTO is unbounded; the Elastic IP and RDS survive but nothing that turns a fresh instance into "prod" is versioned.
- Recommended fix: commit to `deploy/aws/`: the real Caddyfile, a current `user-data.sh` (docker install → S3 env pull → 3 `docker run`s on network `edge`), a sanitized `cutover-cmd.template.json`, and a 1-page `RUNBOOK.md` (instance died / DB died / bad deploy / vendor down — 5 lines each). Enable S3 versioning on the config bucket.
- Verification: rehearsal — boot a scratch EC2 from the committed user-data, point it at a scratch DB, `curl /api/health`.

### [OPS-03] No rollback artifact: images deploy as mutable `:latest` only
- Domain: Reliability (deploy path) / Severity: **S2** / Likelihood: Med / Effort: S / Priority: **P1**
- Location: `deploy/aws/MIGRATION_AWS.md:58-61, 174-176`; `ARCHITECTURE.md:38`
- Evidence: build → tag `equity-terminal:latest` → push ECR → recreate containers. No git-SHA or date tags, no `:prev`. ECR keeps prior *digests* only until any lifecycle cleanup; nothing records which digest was last-good.
- Root cause: single-operator flow optimized for the happy path.
- Why it matters: when a bad image ships, rollback = rebuild an old commit locally on an Apple-Silicon Mac — the exact path where the documented amd64/arm64 trap (MIGRATION_AWS.md:54-57) once shipped a silent no-op deploy. During an outage that is the worst possible moment for a cross-compile.
- Recommended fix: tag every push with the git SHA alongside `:latest` (two `docker tag`/`push` lines in the deploy notes / cutover script); rollback becomes "run the previous SHA tag". Record deployed SHA in the SSM command output or a KV row.
- Verification: `aws ecr describe-images` shows SHA-tagged history; rehearse a rollback by recreating the container from the previous tag.

### [OPS-04] Deploys bypass CI and have no automated post-deploy smoke
- Domain: Reliability (deploy path) / Severity: S3 / Likelihood: Med / Effort: S / Priority: P2
- Location: deploy flow (MIGRATION_AWS.md:174-176); `.github/workflows/ci.yml` (no image build, no deploy gate); memory lesson "curl /api/health right after every backend push" is manual
- Evidence: the image is built from the **local working tree** and pushed by hand — CI green is not enforced, and uncommitted code can ship. CI never builds the Docker image, so image-only breakage (the `python-multipart` class: dep present locally, missing in image) is invisible until prod. The only automatic check after a deploy is the 30-minute uptime cron.
- Why it matters: a broken deploy has up to a 30-min detection window and no forced pre-conditions; combined with OPS-03 there is also no fast undo.
- Recommended fix: (a) add a CI job that `docker build`s `deploy/aws/Dockerfile`, runs it against a Postgres service, and curls `/api/health`; (b) append a 3-attempt health curl + `errors_1h` check to the cutover SSM document so the deploy command itself smokes.
- Verification: delete a dependency from requirements in a branch — CI must go red at the image job.

### [OPS-05] Restore has been proven exactly once (migration day); no recurring restore drill, RDS PITR unverified
- Domain: Reliability (DR) / Severity: S3 / Likelihood: Low / Effort: M / Priority: P2
- Location: `app/backup.py`; `tests/test_backup.py:39-84`; `scripts/restore_backup.py`; `deploy/aws/MIGRATION_AWS.md:169-171`; `app/r2/client.py:45-48`
- Evidence: good news first — dump→encrypt→decrypt→restore round-trips in unit tests, backups are daily (RPO ≤1d), failures alert via errors_1h, and the real R2→RDS restore ran once on 18 Jul, surfacing **three latent DR bugs** (R2 key casing 12496bf, pg8000 TLS b06e64c, sequence resync 0a5ec41) — proof drills find what unit tests can't. Since then: no scheduled restore-verification against the actual R2 ciphertexts; RDS automated-backup/PITR status is not verifiable from the repo (SYSTEM_MAP.md:14: single instance, no replica; the prior audit *recommended* PITR, nothing records it was enabled). Also: MIGRATION_AWS.md:116-118 claims storage can move to S3 by "pointing the existing env vars at S3" — but `r2/client.py` hard-derives `endpoint_url` from `R2_ACCOUNT_ID` (`https://{account_id}.r2.cloudflarestorage.com`); there is no endpoint override env, so that runbook step is not executable as written.
- Recommended fix: (a) confirm/enable RDS automated backups + PITR in console (near-free at this size) and note it in ARCHITECTURE.md; (b) quarterly drill: `restore_backup.py <date>` into a scratch DB, assert row counts vs manifest; (c) add an `R2_ENDPOINT` override to `r2/client.py` or fix the doc.
- Verification: drill output archived; RDS console shows retention >0 and PITR window.

### [OPS-06] Data-integrity sweep findings are stored, never alerted
- Domain: Reliability/Observability / Severity: S3 / Likelihood: Med / Effort: XS / Priority: P2
- Location: `scheduler.py:567-584` (`run_data_integrity`), `app/data_integrity.py` (no record_error/alert path), `app/admin_routes.py:258-274`
- Evidence: the weekly sweep grades fresh data and stores findings to KV; the only consumer is the admin-gated GET. A spike in findings (bad vendor batch, mis-scaled statements — the DAT-01/LIC-HF class that produced live wrong verdicts) reaches no one unless the owner remembers to look.
- Recommended fix: after `store_sweep`, if `n_findings` jumps above a floor or `status != ok`, call `error_log.record_error` (it already feeds the alerting email path); or expose `integrity_findings` in `/api/health` and threshold it with OPS-01's change.
- Verification: seed one absurd row in rehearsal; Sunday sweep triggers the uptime failure.

### [OPS-07] Operator-doc drift (backup cadence, quota plan, universe scope)
- Domain: Reliability (docs) / Severity: S4 / Likelihood: High / Effort: XS / Priority: P3
- Location: `ARCHITECTURE.md:181` ("keeps 8 weeklies" — actual: daily, KEEP=30, `app/backup.py:37`); `scheduler.py:8-25` (header describes 10k/mo plan + Nifty-50 cadence; live plan is Growth ~50k and tier top1000); `scheduler.py:266-270` (results calendar "~500 calls/week" — VISIBLE_UNIVERSE is now 1002); `app/api_budget.py:4` ("~10k dev plan")
- Why it matters: these files are the runbook a future operator (or agent) will trust; two already-superseded docs this cycle (DEPLOY_NOTES) show drift compounds.
- Recommended fix: one doc-sweep commit; keep plan/cadence facts in exactly one place (ARCHITECTURE.md) and reference it.

### [OPS-08] Uptime monitoring covers the API only — the Vercel frontend is unwatched
- Domain: Reliability / Severity: S3 / Likelihood: Low-Med / Effort: XS / Priority: P2
- Location: `.github/workflows/uptime.yml` (single URL); frontend repo has no uptime workflow (`~/equity-terminal/.github/workflows/` = ci.yml only)
- Evidence: known real failure mode — Vercel missed a merge webhook on 20 Jul (deploy silently absent; fixed by empty-commit retrigger). A broken or stale `equityverdict.com` is invisible to all current alerting.
- Recommended fix: add a second probe to uptime.yml: `curl https://equityverdict.com` asserting 200 + a marker string from `index.html`; optionally compare a build-stamp meta tag against the latest main SHA to catch missed webhooks specifically.
- Verification: point the probe at a bogus path in a branch run — workflow fails.

**Positive (Lane A, verified — don't re-flag):** health-based Dhan→IndianAPI failover (`scheduler.py:129-136, 185-190`); IndianAPI escalation when Dhan dies (budget-guarded); `live_prices` last-good serving; migrations fail-open with create_all belt-and-braces (`app/migrations_boot.py`) — right call for a single-operator box; `--restart always` on all containers; backup failure now alerts; CORS fail-closed; pinned requirements.

---

## Lane B — Scale / Cost

### [SCALE-01] Default connection pool (5+10) meets a 40-thread worker — 10× users = 30s pool-timeout 500s
- Domain: Scale / Severity: **S2** / Likelihood: High at 10× / Effort: XS / Priority: **P1**
- Location: `app/database.py:37` — `create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)`; `Procfile:1` / `deploy/aws/Dockerfile:33` (single uvicorn worker, sync endpoints)
- Evidence: no `pool_size`/`max_overflow`/`pool_timeout`/`pool_recycle` → SQLAlchemy defaults: 5 persistent + 10 overflow = **15 connections max**, 30s blocking wait then `TimeoutError`. FastAPI sync routes run on a ~40-thread AnyIO pool; every request holds a session for its full duration (`get_db` yield), and cold list rebuilds hold one for 4–15s. At today's traffic this never bites; at 10× users, >15 concurrent DB-touching requests queue 30s then 500 — which the PERF-04 middleware will now dutifully count as an error storm.
- Root cause: engine config was written for Railway/SQLite-dev days and never sized for prod concurrency.
- Why it matters: it is the first hard concurrency ceiling on the box, well before CPU/RAM at warm-cache load (warm screener is 48ms), and it fails ugly (30s hangs).
- Recommended fix: `create_engine(..., pool_size=10, max_overflow=20, pool_timeout=5, pool_recycle=1800)`. Headroom check: web 30 + scheduler ~5 vs RDS `db.t4g.micro` max_connections ≈ 80–100 — fine. Do NOT also add uvicorn workers without re-checking (each worker duplicates pool + in-process caches; see SCALE-02).
- Verification: `ab -c 40` against a warm endpoint in rehearsal — no 30s-latency cliff; RDS connection count tops out ≈30.

### [SCALE-02] Memory at 10× on the 2GB t3.small: PERF-05 full-table load still open + cache stampede on every deploy
- Domain: Scale (memory — the swap-kill class) / Severity: **S2** / Likelihood: Med / Effort: M / Priority: **P1**
- Location: `app/main.py:344` (`db.query(models.FinancialFact).all()` in `_all_latest_facts`), called by cold `/api/companies` (`main.py:586`) and cold `/api/peer_universe` (`main.py:691`); `_COMPANIES_CACHE`/`_PEER_UNIV_CACHE`/`_TECH_CACHE`/`_RESULTS_CACHE`/`_OWNERSHIP_CACHE` — none has a rebuild lock (only `ranked_visible` does, `signals.py:154`)
- Evidence: the box was resized t3.micro→t3.small after a swap-death outage (SYSTEM_MAP.md:13). PERF-01's lock fixed the worst offender, but every *other* cache is a bare dict: a deploy makes all caches cold at once, and N concurrent cold hits each run `_all_latest_facts` (~10⁵–10⁶ ORM objects at top1000 tier, growing with coverage backfill) plus full CompanyInsight/MarketSnapshot loads — N× multiplication of the largest allocations in the app, on the same box that also runs caddy + the scheduler (whose daily backup builds the entire DB dump in memory, `backup.py:65-81`, and whose COMPUTE_ON_BOOT recompute runs on every deploy).
- Why it matters: at 10× users a deploy during market hours is the OOM re-run: many concurrent cold rebuilds × biggest allocations. Swap (2G) turns it into a latency death spiral first.
- Recommended fix: (a) replicate the ranked_visible double-checked-lock pattern onto the other five caches (mechanical, ~20 lines); (b) close PERF-05: `DISTINCT ON (company_id, concept) ... ORDER BY fiscal_year DESC` or precompute latest-facts alongside Valuation in the scheduler; (c) longer term, move list payloads into tables (the Valuation pattern) so web never rebuilds.
- Verification: two concurrent cold `/api/companies` → one rebuild in logs; `docker stats` RSS peak during a deploy-cold hammering stays <1.2GB.

### [SCALE-03] `/api/health` runs `max(HistoricalPrice.date)` over ~1.2M rows with no usable index — health is not O(1)
- Domain: Scale / Severity: S3 / Likelihood: High (every probe) / Effort: XS / Priority: P2
- Location: `app/main.py:319` (`func.max(models.HistoricalPrice.date)`); `app/models.py:119-133` (`date` has no standalone index; only `UniqueConstraint(company_id, date)` whose btree can't answer a global max cheaply)
- Evidence: top1000 × ~250 trading days × 5y ≈ 1.2M rows scanned per health call; uptime hits it every 30 min (×3 attempts on retries), plus any future monitor. Health also holds a pooled connection while doing it (compounds SCALE-01 under load).
- Recommended fix: cheapest — have the scheduler write `latest_eod_date` into the existing KV heartbeat payload and read that; or add `index=True` to `HistoricalPrice.date` (one additive alembic revision).
- Verification: `EXPLAIN` shows index/KV read; health p99 <20ms under load.

### [SCALE-04] IndianAPI quota cliff ~11 Aug 2026: budget is a static env nothing will re-size, and steady-state burn exceeds the post-downgrade plan
- Domain: Cost / Severity: **S2** / Likelihood: Certain (dated) / Effort: S / Priority: **P1**
- Location: `app/api_budget.py:29-30` (`INDIANAPI_MONTHLY_BUDGET`, default 10000, actual value in `/opt/app.env`); burn sites: `scheduler.py:141-153` (daily core EOD ~110 names), `scheduler.py:219-236` (weekly full ~50×10 + cohort 60×10), `scheduler.py:266-309` (results calendar = full VISIBLE_UNIVERSE ≈ 1002 calls/week), `scheduler.py:598-635` (coverage backfill ≤40×~10/day until complete)
- Evidence: Growth plan ~50k/mo runs to ~11 Aug 2026 then downgrades (per owner's memory notes); live counter 19 Jul: 11,582/50,000 — healthy today. Steady-state model: EOD ≈2.4k + weekly full ≈2k + cohort ≈2.4k + results calendar ≈4.3k + universe/profile jobs ≈ **11–15k+/mo**, i.e. above a 10k dev plan. The pre-flight guard (`indianapi_ingester.py:1434-1443`) only protects against the number in the env: if the env still says 40–50k after the downgrade, every pre-flight passes and the vendor 429s mid-run instead (partial-ingest states, the July-2026 empty-profiles failure mode). Nothing in the system knows the date.
- Recommended fix: (a) calendar the downgrade day: set `INDIANAPI_MONTHLY_BUDGET` to the new plan in ec2.env and restart; (b) pre-shrink the two elastic jobs — results calendar to core-only or fortnightly (−~4k/mo), cohort size via `ROLLING_REFRESH_SIZE`; (c) nicer: let `budget()` prefer a KV override settable through an admin route so re-sizing needs no SSH/restart; (d) burn remaining Growth quota productively before the date (owner's stated intent) — the coverage backfill already does this.
- Verification: `GET /api/admin/api-usage` after downgrade shows usage tracking under the new ceiling; no vendor-429 lines in scheduler logs.

### [SCALE-05] EC2 disk is a slow-fuse outage: 16GB root, repeated `:latest` pulls, no documented prune
- Domain: Cost/Reliability / Severity: S3 / Likelihood: Med (months) / Effort: XS / Priority: P2
- Location: as-built topology (`deploy/aws/MIGRATION_AWS.md:154-176`); no prune step in any committed deploy doc; cutover-cmd.json contents unverifiable (off-repo, see OPS-02)
- Evidence: every deploy pulls a new ~500MB+ image; superseded layers accumulate; caddy logs + docker json logs also grow unbounded by default (no `--log-opt max-size` documented). Disk-full takes down all three containers at once and blocks the SSM fix-deploy too.
- Recommended fix: add `docker system prune -af --filter until=72h` to the cutover command; set `--log-opt max-size=50m --log-opt max-file=3` on the container runs (belongs in the OPS-02 user-data commit); a CloudWatch disk alarm if ever convenient.
- Verification: `df -h /` stays <60% across several deploys.

- **Payload-scale note (PERF-08, still open):** full-universe list payloads (companies 730KB, factors 560KB) are at top1000 tier already; pagination/virtualization remains the prerequisite for the next tier or 10× users on mobile.
- **Frontend bundle (one line, per scope):** entry bundle ≤175kB gzip enforced as a hard CI gate (`scripts/check-bundle-budget.mjs`, ci.yml:41-42) — healthy, covered by UI workstream.

---

## Lane C — Testing / CI

**Inventory.** Backend: 27 test files, ~250 tests (auth incl. TestClient flows, backup round-trip, api_budget, valuation/verdict gates, factors, dhan client, corporate actions, forensics, portfolio/risk, safety 33 tests, universe tiers); CI = pytest on SQLite + parity-fixture regeneration must-not-crash (`.github/workflows/ci.yml`), uptime cron. Frontend: engine parity 60/60 + derive parity 48/48 vs committed fixtures, prod build, bundle budget gate, 5-test Playwright smoke in hermetic seed mode, lint report-only. Cross-repo: versioned pre-push hook (`scripts/hooks/pre-push`) regenerates + verifies + auto-commits fixtures whenever engines/derive/sector_params change — a genuinely good guard. Alembic: exactly one baseline revision (`alembic/versions/974cc2004deb_*`), stamp-or-upgrade at boot.

### [TEST-01] Prod dialect never tested: CI runs everything on SQLite, prod is Postgres
- Domain: Testing/CI / Severity: **S2** / Likelihood: Med / Effort: S / Priority: **P1**
- Location: `.github/workflows/ci.yml:27` (`DATABASE_URL: sqlite:////tmp/ci_terminal.db`); `app/migrations_boot.py`; `app/backup.py:126-139` (postgres-only sequence resync)
- Evidence: the three DR bugs found *live* on migration day — R2 key casing, pg8000 TLS, Postgres sequence resync — are all in the class SQLite CI structurally cannot catch. Postgres-only branches (`dialect.name == "postgresql"`, pg8000 ssl_context path in `database.py:24-36`, JSON column behavior, the `_additive_migrations` ALTERs in `main.py:38-55`) execute for the first time in production.
- Recommended fix: add a second CI job with `services: postgres:16`, `DATABASE_URL: postgresql+pg8000://...`, running the same pytest suite + an explicit boot: `python -c "from app.migrations_boot import run_boot_migrations; assert not run_boot_migrations().startswith('error')"`.
- Verification: job green on main; deliberately break a migration in a branch — job red.

### [TEST-02] Nothing stops a bad migration or bad image reaching prod; migrations are fail-open and rollback-less
- Domain: Testing/CI / Severity: **S2** / Likelihood: Med / Effort: M / Priority: **P1**
- Location: `app/migrations_boot.py:14-17` (error → log + continue with create_all); deploy path (OPS-03/04); one-revision alembic history (downgrade path never exercised)
- Evidence: chain of holes — image built from local tree (CI unenforced) → migrations apply at boot, fail-open (a half-applied ALTER logs loudly and… keeps serving against a wrong schema) → no staging DB → no tagged image to roll back to → data restore is the only rollback (RPO ≤1 day). The fail-open choice is defensible for a single operator (crash-loop is worse), but it means the *only* line of defense is pre-prod testing, which doesn't exist for the Postgres path (TEST-01).
- Recommended fix: (a) TEST-01's Postgres job is 80% of this; (b) add alembic upgrade→downgrade→upgrade round-trip to that job so the first real ALTER isn't the first-ever downgrade test; (c) the docker-build+health-probe CI job from OPS-04; (d) before any risky migration: `RUN_BACKUP_NOW=true` (mechanism already exists, `scheduler.py:724-728`) — write this into the runbook.
- Verification: branch with an intentionally broken revision goes red in CI, never reaches the box.

### [TEST-03] Ingesters — the highest-incident-density code — have zero tests
- Domain: Testing / Severity: S3 / Likelihood: High / Effort: M / Priority: **P1** (highest-value new tests)
- Location: `app/ingest/indianapi_ingester.py` (~1,470 lines vendor-JSON parsing → facts/prices/insights), `app/dhan/backfill.py` (date conversion, split repair), `app/ingest/compute_valuations.py`; `tests/` contains no test_ingest*/test_indianapi*/test_backfill*
- Evidence: the incident log is dominated by ingest bugs: UTC-shifted Dhan dates poisoning histories (needed `RUN_DHAN_REPAIR`), KOTAKBANK split-scaled shares, misfiled equity columns, VEDL stale share count (live wrong BUY, DAT-01). Each was found in prod, none could have been caught by existing tests.
- Recommended fix: golden-fixture tests: commit 3–4 recorded vendor JSON payloads (a bank, a nonfinancial, a malformed/partial one) and assert the exact facts/price/insight rows produced; unit-test the Dhan epoch→IST date conversion across DST-irrelevant but boundary-relevant times (the exact prior bug); test split-ratio back-adjustment on a synthetic series. ~1 day of work, permanently ends the "parser regression found by a wrong live verdict" loop.
- Verification: mutate a parser line — a test fails.

### [TEST-04] No test anywhere exercises the real frontend↔backend contract
- Domain: Testing / Severity: S3 / Likelihood: Med / Effort: S-M / Priority: P2
- Location: `playwright.config.js:20` (`VITE_API_URL: http://127.0.0.1:9` — deliberately unroutable, seed mode); backend TestClient coverage = auth only (`tests/test_auth.py`, `tests/test_email_verification.py`)
- Evidence: the smoke suite's hermetic design is right for its job (catches builds-fine-crashes-at-runtime), but it means a backend field rename (`mos` → anything) ships with every gate green on both repos; the parity harnesses cover math, not response shapes. The 16 Jul incident class is covered; the drift class is not.
- Recommended fix: backend-side contract snapshots — TestClient asserts the key-set (not values) of `/api/health`, `/api/companies` row, `/api/companies/TCS`, `/api/factors` idea, `/api/portfolio` item against committed JSON key lists; frontend imports the same lists in a 20-line vitest. Alternatively a weekly scheduled Playwright run against prod URL, read-only.
- Verification: rename a response field in a branch — backend CI red before the frontend ever sees it.

### [TEST-05] Frontend has no unit tests and lint is non-gating
- Domain: Testing / Severity: S4 / Likelihood: Low / Effort: M / Priority: P3
- Location: `~/equity-terminal/package.json` (no vitest/jest; scripts = dev/build/lint/parity/e2e); ci.yml:33-34 (`npm run lint || true`, ARC-08 ratchet note)
- Evidence: formatters/dataQuality/derive-consumers rely on parity + smoke only; the lint baseline ratchet is documented but un-enforced (count can silently rise).
- Recommended fix: accept for now (smoke+parity+budget is decent for the size); when touching `src/lib/`, add vitest for `formatters.js`/`dataQuality.js`. Turn the lint ratchet into a number: `eslint -f json | jq length` compared against a committed baseline count.

---

## Lane D — Instrumentation

### [INST-01] Zero product analytics — the owner cannot see usage at all
- Domain: Instrumentation / Severity: S3 / Likelihood: — / Effort: S / Priority: **P1** (pre-launch)
- Location: frontend — no hits for plausible/umami/posthog/gtag/matomo/beacon in `src/`, `index.html`, `package.json`; backend — no pageview/event endpoint
- Evidence: the only behavioral signals in the system are the AuthEvent ledger (signups/logins/failures, `app/models.py:34-47`, admin-gated at `/api/admin/auth-events`) and server-side vendor-call counters. Nothing answers: DAU, which of the ~15 views get used, screener→company→valuation funnels, feature deadweight. Every product decision (incl. what to cut for the quota cliff) is currently blind.
- Root cause: DPDP posture correctly rejected Sentry-class third parties (Jul 17); nothing self-owned replaced the product-analytics half.
- Recommended fix (DPDP-compatible, both self-owned + India-resident): EITHER self-host umami/Plausible-CE as a 4th container on the EC2 (cookieless, ~100MB RSS — mind SCALE-02 headroom) OR the 30-line first-party option: `POST /api/beat {view}` → daily per-view counters in the existing kv_store (no IP, no UA, no cookie — not personal data), surfaced via a new `/api/admin/usage`. The latter matches the codebase's error_log pattern exactly.
- Verification: after a week, admin endpoint shows per-view daily counts; no third-party requests in the browser network tab.

### [INST-02] No user-feedback channel — problems surface only as silent churn or an error counter
- Domain: Instrumentation / Severity: S3 / Likelihood: — / Effort: XS / Priority: P2
- Location: no feedback/contact route in `app/*routes*.py`; no form/mailto in `src/` (grep feedback/contact: only prose strings)
- Evidence: for a product whose core risk is "a number looks wrong" (see the wrong-live-BUY incidents), there is no way for a user to tell the owner. errors_1h captures exceptions, not wrong-but-200 answers — the dangerous class here.
- Recommended fix: `POST /api/feedback {text, page, email?}` → kv_store ring buffer + `send_email` to the owner via the existing `app/mailer.py` (already configured for signup mail); a "Report an issue with this number" link on company pages. Rate-limit via the existing general bucket.
- Verification: submit → owner inbox + `/api/admin/feedback` lists it.

### [INST-03] SEO infrastructure is present and sound (minor gaps only)
- Domain: Instrumentation/SEO / Severity: S4 / Likelihood: — / Effort: XS / Priority: P3
- Location: `~/equity-terminal/public/robots.txt` (allow-all + sitemap pointer); `~/equity-terminal/api/sitemap.js` (dynamic, every covered ticker, s-maxage 86400); `~/equity-terminal/api/stock/[ticker].js` (title/meta/OG/canonical + JSON-LD at :103)
- Evidence: the UX-05/#117 build shipped end-to-end. Gaps: sitemap silently degrades to landing-page-only when the API call fails (no retry/stale-cache), no `<lastmod>`, and sitemap correctness depends on the unmonitored frontend (OPS-08).
- Recommended fix: add `lastmod` (today's date is fine at daily changefreq); optionally serve last-good ticker list from a module cache on API failure. Routing/canonicalization is covered by the UI workstream — deferred here.

---

## Summary

1. Best-shape lanes first: graceful degradation, budget guards, backup mechanics, and the cross-repo parity guard are genuinely strong; most of the July audit's P0/P1 ops fixes verifiably landed.
2. The single worst gap is one line of YAML: `scheduler_beat_min`/`price_age_days` exist but uptime.yml alerts only on errors_1h — silent data rot (the product's most likely failure) still pages nobody (OPS-01, XS fix).
3. Production is not rebuildable from the repo: Caddyfile, app.env, cutover command, and user-data all live off-repo on one EC2; rollback is impossible beyond `:latest` (OPS-02/03).
4. Deploys are hand-built from the local tree, bypass CI, and get no automated post-deploy smoke beyond a 30-min cron (OPS-04).
5. Scale ceiling #1 is the default 15-connection SQLAlchemy pool vs a 40-thread worker — at 10× users it manufactures 30s hangs then 500s; one create_engine line fixes it (SCALE-01).
6. The swap-kill memory class is half-closed: `_all_latest_facts` still loads the whole facts table on cold rebuilds and five of six caches have no stampede lock (SCALE-02).
7. A dated cost cliff: IndianAPI Growth downgrades ~11 Aug 2026; steady-state burn (~11–15k/mo at top1000 tier) exceeds the fallback plan and the static budget env won't re-size itself (SCALE-04).
8. CI never touches Postgres, the deploy image, or a migration downgrade — the exact classes of the three bugs found live on migration day; ingesters (highest incident density) have zero tests (TEST-01/02/03).
9. Instrumentation is the emptiest lane: zero product analytics and no user-feedback channel, despite DPDP-compatible self-owned options that fit the existing kv_store/mailer patterns (INST-01/02); SEO infra is fine.
10. Counts: **S2 ×8** (OPS-01/02/03, SCALE-01/02/04, TEST-01/02) · **S3 ×10** (OPS-04/05/06/08, SCALE-03/05, TEST-03/04, INST-01/02) · **S4 ×3** (OPS-07, TEST-05, INST-03). Quick wins: OPS-01 + SCALE-01 + OPS-08 + SCALE-03 are each ≤10 lines.
