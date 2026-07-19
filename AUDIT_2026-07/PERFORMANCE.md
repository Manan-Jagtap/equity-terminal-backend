# Appendix — Performance, Reliability & Observability (Agent 6)

Read-only, gentle GET probing (backfill was running). Good engineering discipline overall: batch-loading replaced the screener N+1, valuations precomputed to a table, every external HTTP call has a timeout, Dhan client has backoff+401 self-heal+graceful degradation, frontend code-splits aggressively (1.4MB pdf.worker deferred). Real risks: heavy uncached list rebuilds on a single-worker 2GB box, single-instance DR blast radius, observability blind spots.

## Measured timings (ap-south-1, cold vs warm)
| Endpoint | Cold | Warm | Payload | Server cache |
|---|---|---|---|---|
| /api/health | 0.038s | — | 29B | none |
| /api/companies?nifty50 | 6.11s | 0.040s | 40KB/53 | 5min |
| /api/companies (full) | ~6.1s | 0.17s | **730KB**/~500 | 5min |
| /api/companies/TCS detail | **0.107s** | — | 24KB | none |
| /api/factors (Alpha) | **15.12s** | — | 560KB | 5min |
| /api/peer_universe | 6.67s | — | 181KB | 30min |
| /api/screen/technical | 4.36s | — | 268KB | 5min |
| /api/results | 1.53s | — | 403KB | **none** |
| /api/ownership | 1.13s | — | 374KB | **none** |
Company detail is healthy (~100ms). Pain is entirely in full-universe list builds + Alpha compute.

---

### [PERF-01] Heavy list endpoints recompute with no shared cache and no stampede lock — top OOM/latency risk
- **Domain:** Performance & Reliability  **Severity:** S2  **Likelihood:** High  **Effort:** M  **Priority:** P0  **Status:** Partially fixed (June C5 removed per-request DCF; marshaling/cold-cache cost remains)
- **Location:** app/main.py /api/factors(331), /api/companies(479), /api/screen/technical(408), /api/peer_universe(583); app/signals.py:143 ranked_visible
- **Evidence:** Cold: /api/factors **15.1s**, peer_universe 6.7s, companies 6.1s, technical 4.4s. `ranked_visible(db)` (15s Alpha) has NO internal cache, called by 7+ sites; /api/factors and /api/baskets keep SEPARATE caches → opening Ideas cold runs the same 15s compute TWICE (~30s). Every cache is a per-process dict with no lock → N concurrent cold requests each rebuild (stampede). Single uvicorn worker, 2 vCPU, 2GB. A 15s pure-Python loop holds the GIL and stalls other requests; concurrent cold rebuilds each materialize large structures → the exact profile that swap-killed the t3.micro. Every deploy makes all caches cold at once.
- **Fix:** (a) Memoize ranked_visible with a short module-level TTL so all callers share one compute; (b) per-cache rebuild lock (double-checked); (c) move Alpha/factor build into the scheduler post-refresh recompute (like Valuation) and have the endpoint read a table.
- **Verification:** Two concurrent cold hits to /api/factors → one rebuild not two; docker stats RSS stays well under 2GB.

### [PERF-02] Scheduler failure, stalled ingests, and data staleness are unobservable — silent data rot
- **Domain:** Reliability & Observability  **Severity:** S2  **Likelihood:** Med  **Effort:** S–M  **Priority:** P1  **Status:** New
- **Location:** scheduler.py (all jobs), app/error_log.py, .github/workflows/uptime.yml
- **Evidence:** Scheduler jobs only log.error to stdout. `record_error` is called ONLY from the web ErrorCaptureMiddleware (main.py:214) — the scheduler is a SEPARATE process and never writes errors_1h. /api/health is served by the WEB container and returns ok regardless of scheduler state. `--restart always` silently restarts a crash-looping scheduler (which re-runs heavy COMPUTE_ON_BOOT each boot).
- **Why it matters:** The most likely failure for a data terminal is silent staleness — a dead scheduler freezes prices/valuations and stops the daily track-record snapshots (the moat) while health stays green. Nobody is paged.
- **Fix:** Scheduler writes a heartbeat + last-success-per-job timestamp to kv_store; expose max data-age in /api/health (prices_age_min, last_full_refresh); threshold it in uptime.yml; route scheduler exceptions through record_error.
- **Verification:** Stop the scheduler in rehearsal; /api/health staleness field grows and uptime workflow fails within one interval.

### [PERF-03] Single-instance blast radius: weekly backups (RPO ≤7d), untested restore, silent backup failure
- **Domain:** Reliability (DR)  **Severity:** S2  **Likelihood:** Med  **Effort:** M  **Priority:** P1  **Status:** New
- **Location:** app/backup.py (Sun 04:00 UTC, KEEP=8), scripts/restore_backup.py
- **Evidence:** Backups run WEEKLY → up to 7 days of data loss on restore, incl. daily Alpha/Consensus/Verdict snapshots that are point-in-time and cannot be reconstructed. run_backup() "never raises", returns {"status":"error"}, but no caller checks it (compounds PERF-02) — backups can silently stop for weeks. Dump built ENTIRELY in memory on the 2GB box, coinciding with the weekly full-refresh. Restore path is a manual CLI never auto-exercised; owner has low technical depth → high manual RTO on an RDS loss.
- **Fix:** (a) Enable RDS automated backups + PITR (RPO minutes, ~free) as primary DR, keep R2 encrypted dump as off-stack copy; (b) daily cadence; (c) scheduled "restore into throwaway DB + assert row counts" smoke test; (d) alert on run_backup() non-ok.
- **Verification:** Restore latest R2 into a scratch DB; assert row counts match manifest + a sample valuation reloads.

### [PERF-04] errors_1h (sole alert signal) undercounts — caught-and-returned 500s bypass it
- **Domain:** Observability  **Severity:** S2  **Likelihood:** Med  **Effort:** S  **Priority:** P2  **Status:** New
- **Location:** app/main.py:202 ErrorCaptureMiddleware, error_log.py, uptime.yml (threshold errors_1h>25)
- **Evidence:** Middleware records only exceptions that PROPAGATE. But the code is heavily defensive: company_detail (main.py:688) catches + returns a 500 JSONResponse; dozens do `except: db.rollback()` and return empty/partial. None increment errors_1h. So the uptime threshold can read 0 while endpoints broadly fail/return empty. Ring buffer MAX_ENTRIES=100 caps a storm.
- **Fix:** Record into error_log where routes catch-and-return-500, or emit a 5xx counter in middleware based on response.status_code>=500 (not just exceptions). Keep DPDP-safe fields.
- **Verification:** Force a handled 500; errors_1h increments.

### [PERF-05] _all_latest_facts loads the entire FinancialFact table into Python
- **Domain:** Performance (memory)  **Severity:** S3  **Likelihood:** Med  **Effort:** M  **Priority:** P2  **Status:** Still open
- **Location:** app/main.py:264 _all_latest_facts (screener, main.py:511)
- **Evidence:** `db.query(FinancialFact).all()` materializes every fact (~500 × dozens of concepts × up to 7y ≈ 10^5–10^6 ORM objects), reduces in Python. Composite index can't help a full-table load. Major contributor to the 6.1s cold screener + peak RSS on the 2GB box.
- **Fix:** Server-side latest-per-group: Postgres `DISTINCT ON (company_id, concept) … ORDER BY … fiscal_year DESC`, or precompute latest facts alongside Valuation.

### [PERF-06] /api/peer_universe N+1: ~1000 round-trips per cold build
- **Domain:** Performance  **Severity:** S3  **Likelihood:** Med  **Effort:** S  **Priority:** P2  **Status:** Regressed pattern
- **Location:** app/main.py:611-623 — `for co in query(Company).join(MarketSnapshot).all():` then per-company _latest_facts(614) + lazy co.market.price(613)
- **Evidence:** .join doesn't eager-load market → lazy SELECT per company; + one _latest_facts per company. ~1000 queries → 6.67s cold.
- **Fix:** Batch facts (single grouped load) + selectinload(Company.market) / reuse price_by_cid map.

### [PERF-07] /api/results + /api/ownership have no cache — full-universe rebuild every request
- **Domain:** Performance  **Severity:** S3  **Likelihood:** Med  **Effort:** S  **Priority:** P2  **Status:** New
- **Location:** app/results_routes.py:20, app/ownership_routes.py:19
- **Evidence:** No cache check (unlike screener/factors). Each request loads all CompanyInsight blobs + MarketSnapshot + Valuation, builds ~500 rows: 1.53s (results) / 1.13s (ownership) EVERY hit; holds a thread+connection each on the single worker.
- **Fix:** Add the same 5-min in-process cache as the screener.
- **Verification:** Two back-to-back calls; second ~40ms.

### [PERF-08] No pagination on large full-universe payloads
- **Domain:** Performance  **Severity:** S3  **Likelihood:** Med  **Effort:** M  **Priority:** P2  **Status:** Still open
- **Location:** /api/companies(730KB), /api/factors(560KB), /api/results(403KB), /api/ownership(374KB), technical(268KB), peer_universe(181KB); Screener.jsx:333 renders all rows unvirtualized
- **Evidence:** Every list returns the whole universe in one payload; sizes grow with UNIVERSE_TIER (a nifty500 flip ~5×'s these).
- **Fix:** Server-side limit/offset (or cursor); windowed/virtualized screener table. Lower urgency (Caddy gzip + caches help) but required before a tier flip.

### [PERF-09] Frontend client fan-out + non-virtualized lists
- **Domain:** Performance (frontend)  **Severity:** S4  **Likelihood:** Med  **Effort:** M  **Priority:** P3  **Status:** New
- **Location:** Company.jsx (21 fetch calls), Screener.jsx:333
- **Evidence:** One company page fans out to 10+ per-ticker endpoints, all landing on the single worker; screener renders all rows unvirtualized.
- **Fix:** Keep tab-gated fetches lazy; consider /api/companies/{ticker}/bundle for the above-the-fold set; virtualize screener.
- **Bundle sizes (verified):** entry 311KB/98KB gzip (OK). Correctly deferred: pdf.worker 1.38MB, pdf 330KB, ChartTerminal 183KB, recharts 314KB. Code-splitting is good.

---

## Positive / non-findings (verified — don't re-flag)
- **Indexing is solid** — index=True on all company_id/ticker/date + composite UniqueConstraints on hot tables. "Missing indexes" is NOT a real issue; cost is app-level full-table loads.
- **All external HTTP calls have timeouts** (15–90s). Dhan client: rate-limit, backoff honoring Retry-After, 401 self-heal, None when unconfigured.
- **Graceful degradation real** — live_prices serves last-good on failure; run_intraday_prices does health-based Dhan→IndianAPI failover.
- **Idempotency** — scheduler jobs are upserts keyed by unique constraints; rolling_cohort deterministic from ISO week.

## Severity counts: S2:4 (PERF-01 is P0) · S3:4 · S4:1

## Quick wins
1. 5-min cache on /api/results + /api/ownership (PERF-07) — XS.
2. Memoize ranked_visible TTL (PERF-01) — one change kills the duplicated 15s Alpha compute — S.
3. Rebuild lock on in-process caches (PERF-01) — prevents stampede memory spikes — S.
4. Alert on run_backup() non-ok + scheduler heartbeat/staleness in /api/health (PERF-02/03) — S.
5. Batch peer_universe (PERF-06) — S.

## Cross-lane observations
- **Security:** app/database.py:23-27 sets ssl.CERT_NONE + check_hostname=False for the RDS pg8000 connection — encryption WITHOUT cert verification (MITM-theoretical). Code comment already flags "tighten to verify-full by shipping the RDS CA bundle." → SEC finding.
- **Infra/cost:** single uvicorn worker under-uses the 2 vCPU but is a deliberate memory tradeoff after the swap death — do NOT naively add --workers without first cutting per-request memory (PERF-01/05) or each worker duplicates caches/tables → OOM again. Durable fix: precompute heavy lists into tables like Valuation.
- **Data-quality:** the ~145 (now ~18 post-backfill) unknown-sector stubs inflate list sizes; tracked in Data lane.
