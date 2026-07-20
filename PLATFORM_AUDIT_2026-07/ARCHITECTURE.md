# Architecture & API Appendix (Lead lane) — 2026-07-20

Scope: API surface, data model, migrations, module architecture, error-handling
hygiene. Security exploitability deferred to the security audit; index/perf
tuning in OPS_SCALE appendix; UI contracts in the redesign work.

### [ARCH-01] Three migration mechanisms; only the ad-hoc one is live — non-additive changes have no path to prod
- Domain: Architecture · S2 / High / M · Status: Partial (ARC-04 added Alembic but it was never wired)
- Location: app/main.py:30-55 (`create_all` + `_additive_migrations()` at import time); alembic/versions/974cc2004deb (baseline only); deploy/aws/Dockerfile:33 (CMD is uvicorn only — **no `alembic upgrade` anywhere in the repo, deploy scripts, or docs**)
- Evidence: grep for `alembic upgrade` across repo returns nothing; the single Alembic revision is a baseline snapshot dated 2026-07-17.
- Root cause: Alembic was introduced as a checkbox (July audit ARC-04) without becoming the execution path; schema evolution still happens via idempotent ALTERs inside `_additive_migrations()` guarded by try/except.
- Why it matters: renames, drops, type changes, and constraint additions cannot ship safely; the additive path is fail-open (TEST-02 lens: a failed ALTER logs and boots anyway, leaving code↔schema drift). One root cause with OPS/TEST lenses — cross-ref TEST-02, not duplicated.
- Fix: make `alembic upgrade head` the container entrypoint step (fail-closed), generate real revisions from now on, keep `_additive_migrations` only as a dev-bootstrap behind an env flag.
- Verification: deploy a rename via an Alembic revision to a staging DB; boot fails loudly if the migration fails.

### [ARCH-02] Production image ships probe scripts, vendor response dumps, and .bak litter
- Domain: Architecture · S3 / Med / XS · Status: New
- Location: app/ingest/ — 12 `probe_*.py` + 7 `probe_*_dump.json` (raw vendor payloads) + 6 `*.bak*` files + `fix_roe_sweep.py`, `purge_stale.py` one-offs; Dockerfile `COPY app ./app` includes all of it.
- Evidence: directory listing (recon §1); e.g. `probe_statements_dump.json`, `bulk_ingester.py.bak.20260601-000713`.
- Why it matters: dead code confuses every future maintainer and audit (DATA-07 found one such path that would write fabricated facts if invoked); vendor dumps in the image are a licensing-hygiene smell (compliance seam).
- Fix: delete or move to a non-shipped `tools/` directory; add `.dockerignore` for `*.bak*`, `probe_*`.
- Verification: image contents list clean; DATA-07's dangerous path gone.

### [ARCH-03] 149 swallowed-exception sites, several on critical write paths
- Domain: Architecture · S2 / High / M · Status: Open (hygiene debt; enabler of ENG-01 and DATA-06)
- Location: `grep -rnE "except( Exception)?:\s*(pass|$)" app/` → 149 sites; critical examples: app/ingest/compute_valuations.py:76,83,153; app/ingest/indianapi_ingester.py:1397; app/main.py:814,833.
- Root cause: resilience-by-silence idiom — many are legitimate degrade-gracefully guards, but the idiom is applied indiscriminately, so real failures (ENG-01's frozen ledger; DATA-06's invisible job deaths) look identical to expected noise.
- Why it matters: this is the *mechanism* behind every "silently broken for N days" incident this audit found. One root cause; ENG-01/DATA-06/OPS-01 are its lenses.
- Fix: triage pass — every swallow on a WRITE path or scheduled job must at minimum `error_log.log()` with a job tag (which feeds errors_1h and therefore the existing alert); pure read-path fallbacks may stay silent with a comment.
- Verification: grep count on write paths → ~0; induced job failure appears in /api/health errors_1h.

### [ARCH-04] KVStore is an untyped JSON dumping ground shared by 17 modules
- Domain: Architecture · S3 / Med / M · Status: New
- Location: app/models.py (KVStore); 17 modules via `_kv_get/_kv_put` (manager_engine, segment_sotp, macro, calibration…).
- Why it matters: no schema versioning, no per-key ownership, silent shape drift between writers/readers (the FM macro blob staleness in ENG-01 was invisible partly because KV blobs carry no updated_at contract).
- Fix: minimal — a `kv_meta` convention ({schema_version, updated_at} envelope) + a registry comment listing every key and its owner module; promote the hottest blobs (manager state, segment store) to typed tables when next touched.
- Verification: envelope present on all writes; staleness queryable.

### [ARCH-05] 44 endpoints have no frontend caller (candidates) — API surface larger than the product
- Domain: Architecture · S4 / Low / S · Status: Unverified (needs access-log confirmation — dynamic path construction in the frontend makes static matching lossy; /api/market/* are known false positives)
- Location: recon set-diff (111 normalized backend routes vs 80 frontend literals); genuine dead-code suspects: `/api/companies/{t}/balance_sheet`, `/api/companies/{t}/cash_flow` (superseded by the statements payload in the detail response), `/api/bse/fetch/{X}`, `/api/admin/beta-state` (a one-off diagnostic).
- Fix: enable 1 week of access-log endpoint counters (self-owned, DPDP-fine), then delete confirmed-dead routes.
- Verification: zero hits over a week → remove; contract untouched.

### API-surface assessment (no finding)
125 endpoints, uniform `/api/*` naming, errors consistently via HTTPException
(one ad-hoc dict shape found), auth on user-scoped routes, admin routes
separated. The retired thesis endpoint is a deliberate contract-preserving
tombstone (app/thesis_routes.py) — correct pattern, not debt. No response-
versioning exists; acceptable for a single first-party client, note for any
future public API. Frontend↔backend engine duplication is the parity-locked
design (60/60, 48/48 harnesses) — a strength, not drift.
