# Appendix — Code Quality, Architecture & Redundancy (Agent 4)

**Baselines (verbatim):** pytest **209 passed** (venv313, sqlite, RATE_LIMIT_AUTH=100). eslint **120 problems (105 errors, 15 warnings)**, no CI lint gate. npm build exit 0 (chunks: pdf 330kB, CartesianChart 314kB, index 311kB, Company 202kB). Parity **60/60** + **48/48** — but see ARC-05 (false confidence). Counts — S0:0 · S1:0 · S2:7 · S3:8 · S4:3.

---

### [ARC-01] Frontend↔backend VERDICT logic diverges, unguarded by parity harness
- **Domain:** Code & Architecture  **Severity:** S2  **Likelihood:** High (already diverged)  **Effort:** S  **Priority:** P1  **Status:** Partially fixed / Regressed (audit C3)
- **Location:** src/lib/recommend.js:22-136 (esp :120) vs app/engines.py:543 and app/main.py:288
- **Evidence:** engines.py:543 dropped "TRIM" (audit C3 fixed at source); recommend.js:120 **still emits verdict="TRIM"**, and main.py:288 keeps a dead `_NORMALIZE_VERDICT={"TRIM":"REDUCE"}`. Client recomputes the full verdict (composite>=68 & mos>0.15 & conf.high → BUY…) as a hand-maintained mirror. Parity harness only covers engine.blended() valuation + derive assumptions — NOT verdict thresholds. So the headline output has silently drifted.
- **Why it matters:** A user can get a different verdict client-side than the server returns; any future threshold change (composite cutoff, MoS gates, ≥0.80 lender-divergence gate) diverges invisibly.
- **Fix:** Add a verdict-parity harness comparing recommend() cases; delete main.py:288 normalizer; align recommend.js to the 5-tier scheme (drop TRIM).
- **Verification:** verdict-parity green in CI; no "TRIM" in recommend.js.

### [ARC-02] "AI Thesis" is a dead feature that still ships a misleading UI
- **Domain:** Code & Architecture (+ trust/compliance)  **Severity:** S2  **Likelihood:** High  **Effort:** S  **Priority:** P1  **Status:** New
- **Location:** src/components/Company.jsx:244 (tab), :1730-1795 (ResearchNoteCard/AIThesisTab); app/thesis_routes.py
- **Evidence:** The stub docstring claims the FE hides the tab on "unavailable" — **it does not**. Tab `{id:"thesis",label:"AI Thesis"}` renders unconditionally; AIThesisTab advertises "AI-written, grounded in the filings… every figure machine-checked" with a "Generate note" button; clicking → /thesis returns unavailable → UI shows "AI research notes are being enabled for this account" (implies coming-soon, but it's permanently retired on an AI-free platform).
- **Why it matters:** Ships a prominent non-functional feature promising AI the platform deliberately doesn't offer; misleading copy. Also cross-lane with CMP-08 marketing/trust.
- **Fix:** Remove the thesis tab + AIThesisTab/ResearchNoteCard + /thesis route, OR replace copy with honest "retired". Correct thesis_routes.py docstring. Blast radius: Company.jsx only.

### [ARC-03] Silent broad-exception swallowing masks real failures
- **Domain:** Code & Architecture  **Severity:** S2  **Likelihood:** Med  **Effort:** M  **Priority:** P2  **Status:** Still open
- **Location:** 20 `except …: pass` sites; heaviest app/ingest/indianapi_ingester.py:728-760 (10 consecutive); also main.py:729,748; compute_valuations.py:76,83,132; _additive_migrations main.py:44-49
- **Evidence:** 284 `except Exception` total, 0 bare, 20 silent-pass with no log. The ingester block drops 10 parse steps silently → a failing vendor field becomes invisible data loss, not an alert.
- **Why it matters:** On a self-owned-telemetry platform, silent pass defeats the observability the team built; ingestion gaps look like "no data" not "broken parse".
- **Fix:** Replace silent pass with log.debug/warning in ingester + compute paths; keep pass only where truly optional (comment why).

### [ARC-04] Triple boot-migration mechanism is a foot-gun
- **Domain:** Code & Architecture  **Severity:** S2  **Likelihood:** Low-Med  **Effort:** M  **Priority:** P2  **Status:** New
- **Location:** app/main.py:29-51, app/migrations_boot.py
- **Evidence:** Boot runs (1) Alembic upgrade fail-open (logs, no crash); (2) create_all(); (3) _additive_migrations() hardcoded `ALTER TABLE … ADD COLUMN buy_date` in `except: pass` (even rollback is pass). Only 1 Alembic revision exists. A failed Alembic upgrade boots anyway on a half-migrated schema; hardcoded ALTER masks all errors.
- **Fix:** Fold buy_date into an Alembic revision, delete _additive_migrations; keep create_all for fresh dev only; wire migration failure to error_log/health.

### [ARC-05] Parity harness gives false confidence — static committed snapshot, partial surface
- **Domain:** Code & Architecture  **Severity:** S2  **Likelihood:** Med  **Effort:** M  **Priority:** P2  **Status:** New
- **Location:** tests/engineParity.mjs, tests/parityCases.json/deriveCases.json, .github/workflows/ci.yml
- **Evidence:** .mjs compares engine.js against a committed JSON snapshot (Jul 17). Backend CI proves the generator runs, but nothing diffs freshly-generated fixtures against committed ones. If engines.py changes and the dev forgets to regenerate+commit parityCases.json, frontend CI passes green against a stale snapshot AND stale engine.js. Coverage limited to blended()+derive — verdict, sector_params, financials.py, data_quality unchecked.
- **Fix:** In CI, regenerate fixtures + `git diff --exit-code`; expand coverage to recommend(). Or generate cases at test time.

### [ARC-06] Onboarding & deploy docs describe retired Railway infra as live
- **Domain:** Code & Architecture (doc drift)  **Severity:** S2  **Likelihood:** Med  **Effort:** S  **Priority:** P2  **Status:** New
- **Location:** HANDOFF.md:38-58, DEPLOY_NOTES.md (entire), ROADMAP_2026-06.md, STRATEGY_AND_ROADMAP_2026-07.md
- **Evidence:** ARCHITECTURE.md:46 correctly says Railway retired 18 Jul; but HANDOFF.md (the "read before touching anything" doc, touched Jul 16) still lists the live backend as up.railway.app on Railway, "Credentials ONLY in Railway env", "Railway Postgres". DEPLOY_NOTES.md is an all-Railway runbook — following it targets dead infra. auth.py:39 gates prod-detect on RAILWAY_ENVIRONMENT (dead env; harmless because postgres check still fires).
- **Fix:** Update HANDOFF.md infra table + DEPLOY_NOTES.md to AWS Mumbai; superseded banner; clean the RAILWAY_ENVIRONMENT echo.

### [ARC-07] Missing high-value tests: valuation/ingestion HTTP paths, verdict parity
- **Domain:** Code & Architecture  **Severity:** S2  **Likelihood:** Med  **Effort:** M  **Priority:** P2  **Status:** Partially fixed (CI now runs the suite)
- **Location:** tests/
- **Evidence:** 209 tests, unit-logic heavy. Auth IS covered. Gaps: no endpoint test for POST /api/companies/{t}/valuation or the precomputed-vs-live fallback (main.py:499-510); no ingester-mapping test (the 10-swallow block); no cross-language verdict parity.
- **Fix:** TestClient tests for valuation endpoints (assert list-vs-detail single-source consistency) + fixture-based ingester-mapping test + verdict parity (ARC-01).

### [ARC-08] eslint baseline: 105 errors, no CI gate
- **Domain:** Code & Architecture  **Severity:** S3  **Likelihood:** Med  **Effort:** M  **Priority:** P3  **Status:** New
- **Location:** frontend src/
- **Evidence:** 120 problems (105 err/15 warn). 33 no-unused-vars (real dead vars/imports); 31 set-state-in-effect + 35 static-components (React-19 perf smells: cascading renders, components re-created each render, e.g. Portfolio.jsx:136). Vite doesn't run eslint; ci.yml has no lint job.
- **Fix:** Fix 33 no-unused-vars, triage set-state-in-effect, add eslint to CI.

### [ARC-09] God-modules & main.py mixing routers with inline routes
- **Domain:** Code & Architecture  **Severity:** S3  **Likelihood:** Low  **Effort:** L  **Priority:** P3  **Status:** N/A
- **Location:** portfolio_routes.py (1359), manager_engine.py (1177), export_routes.py (1108), main.py (882)
- **Evidence:** main.py registers 27 include_router AND defines 14 inline @app routes (companies, factors, baskets, valuation, onepager, strategy…). Four modules >1000 lines.
- **Fix:** Extract inline main.py routes into company_routes.py/screen_routes.py; split 1000+ line modules by concern. Not urgent.

### [ARC-10] Admin/ops endpoints have no frontend UI (curl/cron-only surface) — informational
- **Domain:** Code & Architecture  **Severity:** S3  **Likelihood:** Low  **Priority:** P3
- **Evidence:** No admin panel in FE (only SegmentSOTP.jsx touches /api/admin/segment-*). ~18 admin/ops endpoints reachable only by curl/scheduler. Genuinely-unreferenced reads: GET /api/macro/catalog, /api/mutual-funds/search, /api/portfolio/{engine-ledger,engine-status}, /api/dhan/status, /api/bse/announcements/{t}, POST /api/bse/fetch/{t} (BSE aligns with known "BSE blocked" reality).
- **Fix:** Document as intentional ops endpoints (or build a thin admin page); confirm each has require_admin. Leave market sub-routes.

---

## CLN items from Agent 4 (fold into Cleanup Manifest)
- **CLN-02 (Agent4):** `app/onepager.py` (292 lines) — SHADOWED by the `app/onepager/` package (import resolves to __init__.py), can never be imported. Git-tracked. Also __init__.py docstring has stale "R2 PDFs→Claude" reference. Safe `git rm app/onepager.py`.
- **CLN-06 (Agent4):** `src/lib/fyHelpers.js` — 0 importers. Safe remove, build stays green.
- **CLN-07 (Agent4):** scheduler.py:538-539 `with_llm` gate is dead even if key set (transcript_ingester.py:16-17: params are no-ops, llm_summary never set) + main.py:288 TRIM normalizer dead. Remove both.
- **CLN-08 (Agent4):** engine.js:393 dead assignment `flo=fm` (never read) in the parity-checked core. Cosmetic, remove.
- 9 tracked probe_*.py (git rm --cached), 24 .bak (local delete), venv/ py3.9 stale (236MB), local .db files.

## Prior-audit reconciliation (code)
AUDIT_2026-06: C1 yfinance assumptions **Fixed**; C2 two intrinsics **Fixed** (single models.Valuation read by /api/companies + signals + onepager); C3 TRIM **Partially fixed/Regressed** (backend dropped, FE still emits → ARC-01); C5 compute_valuations dead **Fixed**; §3 thesis.py/extract.py/news Anthropic **Fixed** (deleted); §3 probe ×9 + .bak ×25 **Still open**; §4 onepager inline DCF **Fixed**.
PRODUCT_REVIEW_2026-06: valuation.js second DCF **Fixed** (92-line thin adapter); dead components **Fixed** (40/40 used); CI suite broken **Fixed** (209 passing); traceback disclosure **Mostly fixed** (debug-gated, but {"error":str(e)} still leaks text → SEC-10).

## Cross-lane observations
- Data lane: recommend.js verdict thresholds + ≥0.80 lender gate are a hand-maintained mirror with no parity — TRIM proves divergence. C4/C6/C7 need a valuation verdict.
- Security lane: main.py:689/833 {"error":str(e)} confirmed (= SEC-10); confirm all ~18 admin endpoints have require_admin.
- Data/infra: yfinance pinned for one live call (indianapi_ingester.py:1295); auth.py:39 RAILWAY_ENVIRONMENT stale.
