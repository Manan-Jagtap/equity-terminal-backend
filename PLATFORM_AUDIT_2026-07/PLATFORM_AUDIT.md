# Platform Master Audit — EquityVerdict (2026-07-20)

Read-only diagnosis of everything beneath the UI and the valuation engine:
data pipeline, integrity at rest, the non-valuation engines, API/architecture,
reliability/ops, scale/cost, testing/CI, instrumentation. Companion reports:
`VALUATION_AUDIT_2026-07/` (engine), `SECURITY_AUDIT.md` (exploitability),
the redesign PR (UI). Nothing was changed in this pass.

Appendices in this folder: `DATA_PIPELINE_AND_INTEGRITY.md` (Agents 1–2),
`ENGINES.md` (Agent 3), `ARCHITECTURE.md` (Lead/Agent 4),
`OPS_SCALE_TESTING_INSTRUMENTATION.md` (Agents 5–8). ~60 findings total;
every one carries path:line or endpoint/statistic evidence.

---

## Executive summary

**Three things are live-broken today; one of them is public.**
1. **ENG-01 (S1)** — `manager_engine.py:1039` uses `macro_data` without
   importing it. `macro_regime()` has thrown NameError on every run since
   ~15 Jul; the scheduler swallows it nightly, so the FM engine's macro state
   is frozen AND the **public EngineCall track-record ledger has silently
   stopped appending** — the product's own honesty artifact. One-line fix.
2. **DATA-01 (S1)** — vendor `/stock` resolution is name-based and never
   identity-checked: **VAML and VISL hold byte-identical wrong balance sheets
   in prod right now**, and a forced-re-ingest cohort (RAJESHEXPO, BOSCH-HCIL,
   APOLLO…) loops daily without converging.
3. **DATA-02 (S1)** — every re-ingest **purges the full statement history
   before validating the new payload**; a price-only vendor response
   permanently destroys a 7-year history — observed live on SBILIFE
   (`has_data:false`, 0 years stored).

**The systemic disease behind all three is the same:** signals exist but
nothing alerts (OPS-01 — `/api/health` carries `scheduler_beat_min` and
`price_age_days`, uptime monitoring watches only `errors_1h`), and 149
swallowed-exception sites (ARCH-03) make real failures indistinguishable from
expected noise. The platform can rot silently, and twice already has.

**The good news is genuinely good.** Integrity at rest **passes** (15/15
large-cap articulation exact, zero impossible values, every anomaly honestly
gated). Placeholder/synthetic handling has no unflagged leak. Corporate-action
adjustment is consistent end-to-end. The backtest/calibration layer is honest
by design (append-only, point-in-time, walk-forward). And the headline
coverage number is **stale in the good direction**: measured today, **~87% of
1,001 names carry 5 full P&L years** (the "243" figure predates the daily
backfill); the residual gap is four attributable buckets (34 lender-P&L,
11 insurers, 33 stubs, small mis-identity cohort) with concrete per-bucket
fixes in the DATA appendix.

**Production-readiness posture:** capable at current scale, but operating
without instruments — no alert on data freshness, no rollback artifact
(mutable `:latest` only), prod not rebuildable from the repo (Caddyfile/env/
cutover live only on the EC2), deploys bypass CI from the local tree, CI never
touches Postgres or a real migration, zero product analytics, no user-feedback
channel. A dated cost cliff lands **~11 Aug 2026** (IndianAPI plan downgrade;
the static budget env will not re-size itself — SCALE-04). Fix the P0 trio
and the alerting line this week; the platform is then honest again; the rest
sequences below.

### One-paragraph verdict per domain

- **Data pipeline — B−:** ingestion design (idempotent upserts, per-name
  rollback, quota guard, failover) is sound; two S1 write-path defects
  (identity, purge-before-validate) and an un-metered ~85% of true vendor
  spend (DATA-04) undermine it.
- **Data integrity at rest — PASS:** the cleanest lane; the numbers the
  engines read are internally consistent and honestly flagged.
- **Engines (non-valuation) — B:** cores correct (risk math, forensics
  formulas, honest backtests); one live S1 (macro import), a mislabeled
  Alpha factor that double-counts the Street (ENG-02), and a tail of
  honesty leaks (delisted calls frozen at 0%, two XIRRs, price-only
  benchmark) that are individually small and collectively erode trust.
- **Architecture/API — B:** uniform API, parity-locked shared math, sane
  error shapes; the migration story is a foot-gun (Alembic decorative,
  additive-only, fail-open) and hygiene debt (149 silent excepts, shipped
  litter) is the enabler of the silent-rot class.
- **Reliability/Ops — C+:** strong mechanics (backups run, heartbeat exists,
  degradation paths thought through), but look-only health, no restore
  drill, no rollback, no runbook-from-repo — one EC2 and one operator away
  from an unrecoverable bad day.
- **Scale/Cost — B−:** fine at current load; three specific 10× ceilings
  (15-conn pool vs 40 threads; full-table fact loads + cache stampede on the
  2GB box; non-O(1) health query) and the Aug-11 quota cliff.
- **Testing/CI — C+:** parity harnesses + Playwright smoke + budget gate are
  real; but SQLite-only CI, fail-open migrations, zero ingester tests, and
  no contract test = the highest-incident code is the least protected.
- **Instrumentation — D:** flying blind by design (DPDP-compatible self-owned
  options exist and fit existing patterns); SEO infra is fine.

---

## System + data-flow map

```
IndianAPI (fundamentals/insights/news, quota-metered)──┐
Dhan (EOD+intraday prices, LTP; TOTP self-renews) ─────┤
BSE filings (results PDFs → pl_parser) ────────────────┼─► scheduler.py (15 jobs, 01:00–04:00 IST chain + 90-min intraday)
yfinance (narrow intraday fallback) ───────────────────┘        │ per-name rollback isolation; heartbeat → KVStore
                                                                ▼
                    Postgres (RDS): Company · FinancialFact · HistoricalFinancial ·
                    HistoricalPrice/PricePoint · MarketSnapshot · CompanyInsight ·
                    Valuation (precomputed) · AlphaSnapshot/ConsensusSnapshot/
                    VerdictSnapshot/EngineCall (append-only ledgers) · KVStore (17 modules)
                                                                │
        engines: derive→engines (valuation, parity-locked w/ FE) · factors (Alpha) ·
        manager_engine (FM) · backtest · sentiment · forensics · risk analytics
                                                                │
                    FastAPI app (125 endpoints, 27 routers) ── /api/health (beat, price-age, errors_1h)
                          │                                         ▲ uptime.yml alerts on errors_1h ONLY
                          ▼
        Vercel FE (React, parity engine.js) · SSR /stock/* · sitemap · one-pager PDF (backend)
        Backups: nightly 04:00 encrypted → R2 (KEEP=30) · deploy: local buildx → ECR :latest → SSM cutover
```

---

## Prioritised backlog (P0 → P3)

| P | ID | S | Domain | Title | Effort |
|---|----|---|--------|-------|--------|
| **P0** | ENG-01 | S1 | Engines | `macro_data` import missing — FM macro dead, public ledger frozen | **XS** |
| **P0** | DATA-02 | S1 | Data | Purge-before-validate destroys history (SBILIFE live) | S |
| **P0** | DATA-01 | S1 | Data | Name-resolution identity check (VAML/VISL contamination live) | S |
| **P0** | OPS-01 | S2 | Ops | Alert on `scheduler_beat_min` + `price_age_days` (one YAML line) | **XS** |
| **P1** | SCALE-04 | S2 | Scale | IndianAPI downgrade ~11 Aug: budget re-size + burn plan | S |
| **P1** | DATA-03 | S2 | Data | Per-year lender P&L supplement (the "financial edge" is empty) | M |
| **P1** | DATA-04 | S2 | Data | Meter `_get_safe`/direct-ingest vendor calls (85% uncounted) | S |
| **P1** | SCALE-01 | S2 | Scale | Pool 5+10 vs 40 threads → size pool/pre-ping (one line) | **XS** |
| **P1** | ENG-02 | S2 | Engines | Rename/replace consensus-upside "growth" factor | S |
| **P1** | OPS-02 | S2 | Ops | Commit Caddyfile/user-data/cutover; prod rebuildable from repo | S |
| **P1** | OPS-03 | S2 | Ops | Immutable image tags + previous-tag rollback | S |
| **P1** | ARCH-01 | S2 | Arch | Wire Alembic as the real, fail-closed migration path | M |
| **P1** | ARCH-03 | S2 | Arch | Swallowed-exception triage: write-paths must log to errors_1h | M |
| **P1** | TEST-01 | S2 | Test | CI job on Postgres (prod dialect) | S |
| **P1** | TEST-02 | S2 | Test | Migration gate + post-deploy smoke in the release path | M |
| **P1** | SCALE-02 | S2 | Scale | PERF-05 full-table load + cache stampede locks | M |
| **P2** | TEST-03 | S3 | Test | Ingester regression tests (fixture payloads incl. partial/garbled) | M |
| **P2** | ENG-05/06/07 | S3 | Engines | Backtest honesty leaks (delisted 0%, HOLD-as-short, trust-by-sector) | S |
| **P2** | ENG-08/09 | S3 | Engines | One XIRR basis; TRI benchmark | S |
| **P2** | ENG-10/11 | S3 | Engines | LTCG boundary + exemption double-count | S |
| **P2** | DATA-05..09 | S3 | Data | FY labels, alert-gap lens, dead paths, magnitude floors, failover note | S–M |
| **P2** | OPS-05/06/08 | S3 | Ops | Restore drill cadence; integrity-sweep alert; watch the FE | S |
| **P2** | SCALE-03/05 | S3 | Scale | O(1) health; disk headroom/prune | XS–S |
| **P2** | INST-01/02 | S3 | Inst | Self-owned analytics + feedback channel (DPDP-fit) | M |
| **P2** | ENG-12/13/14/16 | S3 | Engines | Sloan sign, sentiment negation/expiry, factor-robustness, risk-analytics inputs | S–M |
| **P3** | ARCH-02/04/05 | S3/4 | Arch | Ship litter, KV envelope, dead-endpoint confirm+prune | XS–M |
| **P3** | ENG-15/17/19, OPS-04/07, TEST-04/05, INST-03, INTG-03/04 | S3/4 | — | Remaining minors (see appendices) | XS–M |

## Quick Wins (high value, ≤ ~10 lines each)
1. **ENG-01** — one import line; unfreezes the public track-record ledger.
2. **OPS-01** — one uptime.yml line; silent data rot starts paging.
3. **SCALE-01** — `create_engine(pool_size, max_overflow, pool_pre_ping)`.
4. **SCALE-03** — index/O(1) the health staleness probe.
5. **OPS-08** — add the Vercel frontend to uptime monitoring.
6. **ARCH-02** — delete probe/dump/.bak litter + `.dockerignore`.

## Prior-audit reconciliation (summary — detail per appendix)
- **Data lane:** 9 Fixed · 2 Partial (June C6 lender P&L → DATA-03; alias map
  → 6 demonstrably failing entries) · 2 Open. Coverage headline superseded
  (243 → ~870 full-P&L).
- **Engines lane:** FM_ENGINE_CHECKLIST corrections noted in ENGINES.md;
  calibration/backtest items delivered honestly; macro leg regressed (ENG-01).
- **Ops lane:** July audit's P0/P1 ops fixes verifiably landed (backups,
  heartbeat, budget guard, CI gates); the alerting *consumption* of those
  signals is the remaining gap (OPS-01).
- **Architecture:** ARC-04 (Alembic) Partial — installed, never wired
  (ARCH-01). ARC-02 thesis retirement verified as a clean tombstone.

## Remediation sequencing
- **Week 1 (stop the bleeding):** P0 row — ENG-01 import + backfill check on
  the frozen ledger; DATA-02 parse-before-purge; DATA-01 identity check +
  quarantine & re-ingest the contaminated cohort; OPS-01 alert line. Then the
  three XS quick wins.
- **Week 2–3 (make failure visible & releases safe):** ARCH-03 triage,
  OPS-02/03 (rebuildable + rollback), TEST-01/02, SCALE-01/02, DATA-04,
  SCALE-04 before **Aug 11**.
- **Month 2 (finish the data story):** DATA-03 lender P&L, ingester tests
  (TEST-03), remaining DATA S3s — this plus the valuation audit's VAL-05
  archetype models is what turns coverage into confident calls.
- **Then:** engines S3 tail, instrumentation, architecture debt.
- **Master-plan note:** this backlog must be merged with the security,
  valuation (VAL-01..11), and UI backlogs into one sequence — highest-harm,
  real-money-facing first: data-integrity S1s and the valuation P0s already
  shipped outrank everything cosmetic.
