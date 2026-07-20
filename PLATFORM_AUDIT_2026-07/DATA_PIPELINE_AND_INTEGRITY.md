# Agent 1+2 — Data Pipeline & Data Integrity Audit (20 Jul 2026)

Read-only audit of the ingestion pipeline, scheduler, coverage, corporate actions,
normalization, freshness/lineage, at-rest integrity, and placeholder handling.
Backend repo at `/Users/manan_jagtap/Downloads/backend` (branch `redesign/phase-0`, clean),
cross-checked against the live prod API `https://api.equityverdict.com`.
~110 prod requests total, throttled ≥0.35 s; no writes, no state-changing calls.
Method: full read of the ingest/scheduler/quality modules + a full-universe scan of
`GET /api/companies` (1,001 rows) + a 73-name stratified `/financials` probe
(56 random across 6 tiers + 17 targeted problem names) + 15-large-cap articulation checks.

Severity scale: S0 = live wrong output to users · S1 = data loss / wrong data at rest ·
S2 = degraded correctness or broken control · S3 = hygiene / latent risk · S4 = informational.

---

## Coverage Breakdown (headline artifact)

### Full-universe hard counts (from `GET /api/companies`, n = 1,001, 2026-07-20)

| Field | Coverage |
|---|---|
| Live price | 1,001 / 1,001 (0 synthetic ₹1.0 sentinels visible) |
| Book equity | 980 (9 of those legitimately negative: IDEA, TTML, ALOKINDS, RENUKA…) |
| Net profit (PAT) | 979 |
| Shares outstanding | 978 |
| Revenue (non-financials) | 855 / 876 |
| Intrinsic value computed | 941 |
| Verdicts | AVOID 312 · LOW CONF 309 · REDUCE 134 · HOLD 128 · ACCUMULATE 61 · NO DATA 33 · BUY 24 |
| Sector still "Unknown"/blank | 13 |

### Statement depth by tier (73-name `/financials` probe; unbiased 56-name random stratum extrapolated)

"Full P&L year" = top line (revenue / interest income / total income) **and** PAT both present.

| Tier | Size | Sampled | 5-yr full P&L | 3–4 yr | 1–2 yr | 0 yr | Est. 5-yr-full in tier |
|---|--:|--:|--:|--:|--:|--:|--:|
| Nifty 50 | 50 | 8 | 7 | 0 | 0 | 1 (HDFCBANK) | ~44 / 50 |
| Nifty Next 50 | 50 | 8 | 8 | 0 | 0 | 0 | ~50 / 50 |
| Midcap 150 | 150 | 10 | 10 | 0 | 0 | 0 | ~150 / 150 |
| Nifty 500 rest | 250 | 10 | 10 | 0 | 0 | 0 | ~250 / 250 |
| Microcap 250 | 251 | 10 | 6 | 2 | 1 | 1 | ~151 / 251 |
| AMFI next 250 | 249 | 10 | 9 | 0 | 0 | 1 | ~224 / 249 |
| **Total** | **1,000** | **56** | | | | | **≈ 869 / 1,000 (≈ 87%)** |

**The brief's "~243/1001 with full multi-year P&L" is stale.** The daily coverage
backfill (`scheduler.py:598-635`, `run_coverage_backfill`, 20:30 UTC, batch 40) has been
clearing stubs since ~17 Jul; measured today, ~87% of the universe carries 5 full
P&L years and ~90% carries ≥3. The residual gap is concentrated and attributable:

| Bucket | Size | Cause | Class |
|---|--:|---|---|
| **A. Banks — partial P&L all years** | 34 (valuation_sector BANK) | `/stock`'s Reuters INC block yields only pbt/tax/pat for lenders. The `/statement` supplement (`indianapi_ingester.py:826-894`) is latest-year-only by design, and for the largest banks wrote **nothing**: HDFCBANK, ICICIBANK, SBIN, KOTAKBANK show PL keys `['pat','pbt','roe','tax']` for **all 5 years** — zero years with interest income/NII/opex/provisions. DCBBANK got exactly 1 year. | Source limitation + supplement design (see DATA-03) |
| **B. Insurers — zero statements** | 11 (SBILIFE, HDFCLIFE, LICI, ICICIPRULI, GICRE, NIACL, ICICIGI, STARHEALTH, GODIGIT, NIVABUPA, CANHLIFE) | `/stock` carries no INC/BAL/CAS for insurers; the `_insurer_statements` fallback writes only a single current-year row set and currently writes nothing (SBILIFE live: `has_data: false`, `years_available: []` while facts/verdict exist from FinancialFact). The per-ingest purge (DATA-02) then leaves 0 years at rest. | Source limitation + purge-before-validate |
| **C. NO DATA stubs** | 33 | 12 still sector="Unknown" (never resolved at the vendor). **6 of those 12 have `VENDOR_QUERY_ALIAS` entries that demonstrably did not work** (AGARWALEYE, AGL, CCAVENUE, EQUITASBNK, RAIN, TI — `indianapi_ingester.py:955-968`); the rest have prices + partial facts but missing equity/shares. | Alias gap / vendor name-resolution failure |
| **D. Recent listings, 3–4 yr** | ~2/10 of microcap sample (HEMIPROP, WEBELSOLAR at 4 yr) | Vendor honestly has fewer fiscal years. | Source limitation (honest) |
| **E. Mis-resolved identities** | ≥2 confirmed (VAML, VISL), ~5 suspected (RAJESHEXPO, BOSCH-HCIL, APOLLO, ASHOKA, CLEANMAX cohort) | Both VAML and VISL carry **byte-identical, absurd balance sheets** (net_worth −0.07, total_assets 0.02 ₹cr, shares 390.42) — the name-based `/stock` lookup resolved two different tickers to the same wrong vendor entity. See DATA-01. | Parser/resolution bug, wrong data at rest |

### Concrete expansion path (per bucket)
1. **A (banks):** loop `_financial_pl_supplement` over every stored fiscal year using
   `/statement profit_loss` full-history columns (it returns a Screener-style multi-year
   table) instead of latest-year-only; for the big banks whose annual-scale guard rejects,
   log the rejection at WARNING (today it silently `continue`s) and fall back to BSE XBRL
   quarterly aggregation. This alone converts 34 partial names to full and unblocks the
   NII/cost-to-income analytics the product calls its edge.
2. **B (insurers):** persist `/statement` rows per fiscal year (the response carries
   year-labelled columns), and exempt statement-less templates from the pre-ingest purge.
3. **C (stubs):** for the 12 Unknowns, verify each alias by hand once against the vendor
   (one `/stock` call each), and add a post-resolution identity check (DATA-01 fix) so a
   wrong resolution is rejected rather than stored; names the vendor genuinely lacks
   should be marked `vendor_uncovered` so the daily backfill stops burning quota retrying.
4. **E:** DATA-01 fix (identity check) + forced re-ingest of the 7-name cohort.

---

## Findings

### [DATA-01] Name-based vendor resolution is never verified — cross-company contamination live at rest
- Domain: Sources/Connectors · Severity: **S1** · Likelihood: Medium (long-tail names) · Effort: S · Priority: **P1**
- Location: `app/ingest/indianapi_ingester.py:971-990` (`_fetch_stock`), `:982-985` (acceptance check)
- Evidence: `_fetch_stock` accepts any payload with `companyName or currentPrice or
  stockDetailsReusableData` — it never checks that the returned company **is** the requested
  ticker. Live proof: `/api/companies/VAML/financials` and `/api/companies/VISL/financials`
  return **identical** balance sheets (net_worth −0.07, total_assets 0.02, borrowings ~0.06-0.08 ₹cr)
  and the list rows share shares_outstanding 390.42 — two different companies stored as clones
  of the same (wrong, micro-scale) vendor entity. The codebase itself documents prior instances:
  `app/dhan/backfill.py:126-131` ("APOLLO→Apollo Hospitals vs Apollo Micro… BAJAJ-AUTO ₹1,020 vs ₹10,156").
  The five forced re-ingest names (`scheduler.py:594` INITIAL_REINGEST) still show PE < 3 and
  MoS 5–117× in prod (RAJESHEXPO mos = 116.6, BOSCH-HCIL pe = 1.41) — re-ingesting cannot fix a
  mis-resolution, so the daily backfill loops on them forever.
- Root cause: `/stock` is a fuzzy name-search API; the ingester treats the first plausible hit as truth.
- Why it matters: wrong fundamentals at rest for real listed companies; the LOW-CONF gate hides the
  verdict but the Financials/Ratios tabs still render the wrong company's numbers; quota is burned
  daily retrying names that will never converge.
- Recommended fix: after `_fetch_stock`, compare the payload's NSE/BSE symbol (present in
  `stockDetailsReusableData`/`companyProfile`) with the requested ticker; on mismatch, treat as
  failure and mark the company `vendor_unresolved` (skip from `needs_fundamentals`). Add a
  cross-company duplicate-statements check to `data_integrity.run_integrity_sweep` (it already
  checks duplicate names; add duplicate BS fingerprints).
- Verification: VAML/VISL/APOLLO re-ingest either resolves to the right entity or stores nothing;
  integrity sweep shows zero identical-BS pairs.

### [DATA-02] Purge-before-validate: a partial vendor payload permanently destroys multi-year history
- Domain: Sources/Connectors · Severity: **S1** · Likelihood: Medium · Effort: S · Priority: **P1**
- Location: `app/ingest/indianapi_ingester.py:1026-1030` (purge), `:982-985` (weak acceptance)
- Evidence: `ingest_company` deletes ALL `HistoricalFinancial` rows for the company
  (`s.query(models.HistoricalFinancial).filter_by(company_id=co.id).delete(...)`) **before**
  `_parse_financials` knows whether the fresh payload contains any statements. The acceptance
  check in `_fetch_stock` passes on `currentPrice` alone — so a vendor response that has a price
  but an empty/absent `financials` block wipes a previously-good 7-year history and leaves 0
  years (the insurer fallback then writes at most one calendar-year row set). Live evidence:
  SBILIFE `has_data:false`, 0 statement years, while FinancialFact rows from an earlier pass
  (equity ₹19,022 cr, PAT ₹529 cr) still drive a HOLD verdict — facts survive, statements don't.
- Root cause: purge was added to clear stale yfinance rows (comment at :1026-1028) and runs
  unconditionally on every re-ingest.
- Why it matters: the weekly full refresh + daily backfill re-ingest hundreds of names; any
  transient vendor degradation (missing financials block) converts full coverage into 0-year
  coverage silently. This is the most probable mechanism behind bucket B and part of the
  historical "243/1001" trough.
- Recommended fix: parse first into memory; only purge+rewrite when the new payload yields
  ≥ N (e.g. 3) fiscal years, else keep the old statements and log a WARNING. (The purge and
  write are already in the same transaction, so this is a small reorder.)
- Verification: unit test — feed a payload with `currentPrice` only against a seeded company;
  statements must survive.

### [DATA-03] Lender P&L supplement is latest-year-only and silently no-ops on the largest banks — the declared "financial edge" is still unpopulated (June C6 remains half-open)
- Domain: Coverage/Normalization · Severity: **S2** · Likelihood: High (34/34 banks affected) · Effort: M · Priority: **P1**
- Location: `app/ingest/indianapi_ingester.py:826-894` (`_financial_pl_supplement`), `:871-875` (silent `continue`)
- Evidence: prod today — HDFCBANK/ICICIBANK/SBIN/KOTAKBANK have 5 PL years each containing only
  `pat, pbt, roe, tax`; zero years with `interest_income/nii/opex/provisions`. DCBBANK has the
  supplement for exactly 1 year. The function (a) targets only the latest fiscal year, and
  (b) when the annual-scale guard trips (`ii <= pat_annual` etc.) it `continue`s without any
  log, so a permanently-failing bank looks identical to "no data".
- Why it matters: cost-to-income, NIM-proxy, provisioning-trend analytics are impossible; the
  RI valuation runs on facts so verdicts survive, but the product's stated differentiator
  ("sector-correct financials that are actually populated") is not true for any large bank.
- Recommended fix: pull `/statement profit_loss`'s full multi-year table and upsert per year
  (still additive, same identity guards per year); log guard rejections at WARNING with the
  rejected values.
- Verification: HDFCBANK `/financials` shows `interest_income/nii/provisions` for ≥4 of 5 years.

### [DATA-04] Vendor-quota accounting under-counts ~85–90% of real spend — the budget guard governs with wrong numbers
- Domain: Scheduler/Connectors · Severity: **S2** · Likelihood: High (every full ingest) · Effort: S · Priority: **P2**
- Location: `app/ingest/indianapi_ingester.py:61-84` (`_get` increments `_CALL_COUNT`) vs
  `:283-303` (`_get_safe` does **not**); `run()` at `:1459-1463` records only the `_get` delta;
  `scheduler.py:377-387` (`run_universe_refresh` → `ingest_company` direct, records nothing),
  `scheduler.py:927-963` (`_ensure_universe` boot ingest, records nothing)
- Evidence: a full ingest ≈ 1 `/stock` + ~9–14 `_get_safe` insight/supplement calls
  (`api_budget.CALLS_PER_FULL_INGEST = 10` acknowledges this), but only the 1–4 `_get` calls
  reach `record_usage`. The pre-flight projection is right; the persisted monthly tally
  (`models.ApiUsage`) is a fraction of true vendor-side usage, so `would_exceed` passes runs
  the vendor may refuse. `run_results_calendar` (scheduler.py:304) records usage only if the
  whole loop completes.
- Why it matters: the July-2026 quota exhaustion incident is exactly the failure this guard
  exists to prevent; with the Growth plan's ~Aug-2026 downgrade approaching (memory), accurate
  metering becomes load-bearing again.
- Recommended fix: increment `_CALL_COUNT` in `_get_safe` too (single line), and wrap
  direct `ingest_company` call sites with a `record_usage` of the measured delta.
- Verification: after a 1-name full ingest, ApiUsage grows by ~10–15, not 1–4.

### [DATA-05] Fallback statement rows are labelled with the calendar year, not the fiscal year
- Domain: Normalization (FY alignment) · Severity: S3 · Likelihood: Medium (Jan–Mar ingests) · Effort: S · Priority: P3
- Location: `app/ingest/indianapi_ingester.py:899-903` (`_insurer_statements`:
  `year = datetime.date.today().year`), `:834-836` (`_financial_pl_supplement` same fallback)
- Evidence: `/statement` responses carry no explicit year in this path; rows are upserted under
  today's calendar year. Ingest between Jan–Mar stamps FY-ending-March-previous data one year
  forward, and successive years of re-ingests create phantom "years" whose values are actually
  the same trailing period re-labelled.
- Recommended fix: derive the FY label from the response's own period columns where present,
  else `year - 1` when month < April (Indian FY convention); never today's bare year.
- Verification: freeze time to Feb in a unit test; row lands under the prior FY.

### [DATA-06] Scheduler job failures are invisible to the health/alert surface (log-only), except backups
- Domain: Scheduler · Severity: S3 · Likelihood: Medium · Effort: S · Priority: P2
- Location: `scheduler.py:152-153, 194-195, 235-236, 308-309, 537-538` (all `log.error` only)
  vs `scheduler.py:648-661` (backup is the ONLY job wired into `error_log.record_error`);
  `app/main.py:291-326` (`/api/health` fields)
- Evidence: `/api/health` exposes `errors_1h` (web-process error ledger), `scheduler_beat_min`
  (liveness) and `price_age_days` (price staleness). A scheduler that is alive but whose
  `run_full`/`run_compute`/`run_coverage_backfill` fails every time keeps beating and keeps
  prices fresh — fundamentals/valuation staleness has **no** health signal and job exceptions
  never reach `errors_1h`. Mitigations already present: heartbeat (PERF-02), the weekly
  `data_integrity` sweep (price_stale flags), the dashboard cross-check. Live health today:
  `{"errors_1h":0,"scheduler_beat_min":1,"price_age_days":3}` (3 = Fri close on a Monday, normal).
- Recommended fix: reuse the backup pattern — on any scheduled job's exception, `record_error`
  with a `scheduler/<job>` scope so `errors_1h` (the sole uptime-alert signal) fires; optionally
  add a `valuation_age_days` field to /api/health (max Valuation.updated_at).
- Verification: force one job to raise; `errors_1h` increments within the hour.

### [DATA-07] Dead/stale ingestion code shipping in the package — including a manual-figures path that would write fabricated "real" facts
- Domain: Sources/Connectors hygiene · Severity: S3 · Likelihood: Low (needs manual run) · Effort: S · Priority: P3
- Location: `app/ingest/bse_results_ingester.py:144-203` (`update_nbfc_metrics_manually` —
  hardcoded "FY26 Q4" AUM/GNPA/NIM for 5 NBFCs with the comment "REPLACE THESE WITH REAL
  NUMBERS"; `__main__` runs it by default at :206-213); duplicate stale twin at
  `app/bse_results_ingester.py`; `app/ingest/indianapi_ingester.py:1341-1370` (`_yf_live_prices`,
  no callers — the retained yfinance path is dead; `run_intraday` at :1373 uses IndianAPI);
  `app/ingest/fix_roe_sweep.py:14` (imports yfinance + the long-gone `bulk_ingester`);
  ~9 `probe_*.py` + `*_dump.json` + `*.bak*` files still in `app/ingest/`;
  `app/models.py:103` (`HistoricalFinancial.source` default still `"yfinance"`).
- Evidence: no imports of `bse_results_ingester`/`_yf_live_prices` anywhere outside their own
  modules (grep over app/ + scheduler.py). The manual NBFC dict upserts under `fy=2026` with
  `source="bse_api"` — if anyone runs the module (its docstring invites it), placeholder ratios
  become indistinguishable from ingested facts.
- Recommended fix: delete both bse_results_ingester copies (BSE announcements are blocked
  anti-bot anyway — see memory/Prior-issue list), `_yf_live_prices`, `fix_roe_sweep.py`, probe
  scripts and .baks; flip the `source` default to `"indianapi"`.
- Verification: `pip-compile`/grep shows yfinance importable only from tests; package tree clean.

### [DATA-08] No ingest-time magnitude/unit validation on `/stock` statements; parse-failure logging is invisible at prod log level
- Domain: Normalization · Severity: S3 · Likelihood: Medium · Effort: M · Priority: P2
- Location: `app/ingest/indianapi_ingester.py:182-254` (`_parse_financials` — stores any float),
  contrast `app/ingest/pl_parser.py:171-219` (`validate_pl` has magnitude floors — but only the
  BSE-PDF path uses it); `:735-740` (`_field` logs failures at DEBUG; `scheduler.py:30` sets INFO)
- Evidence: VAML/VISL stored total_assets = 0.02 ₹cr (₹2 lakh) for a listed company — no floor
  fired at ingest. Units are assumed ₹cr end-to-end (`FinancialFact.unit="INR_CR"` hardcoded at
  :179); the vendor's own scale is never asserted. Downstream gates (metrics bands,
  `_sanitize_statements`, data_quality) protect verdicts but the raw statements still display.
  Separately, ARC-03's `_field` diagnostic (insight-parse failures) logs at DEBUG while both
  processes run at INFO — persistently-failing vendor fields remain invisible data loss.
- Recommended fix: port `validate_pl`-style floors (top line ≥ ₹10 cr for an indexed name,
  BS non-degenerate) into `_parse_financials`; on failure store nothing and log WARNING.
  Bump `_field` to WARNING on repeated failure.
- Verification: re-ingest VAML → statements rejected, not stored.

### [DATA-09] Intraday failover blast radius: when Dhan is down intraday, only ~53 of 1,001 names refresh
- Domain: Scheduler · Severity: S4 (informational, by design) · Likelihood: Low · Effort: — · Priority: P4
- Location: `scheduler.py:156-195` (`run_intraday_prices` fallback → `run_intraday`),
  `app/ingest/indianapi_ingester.py:1386-1389` (scope = `UNIVERSE` = Nifty 50 + 3 extras)
- Evidence: the health-based fallback polls IndianAPI for `UNIVERSE` only; the other ~950
  visible names keep their last price until the 10:15 UTC EOD job's Dhan-outage escalation
  (`scheduler.py:131-136`). Documented trade-off (quota); worth stating in the ops runbook —
  and note the intraday fallback's calls are also un-metered (DATA-04: `refresh_price` →
  `_fetch_stock` → `_get` counts in-process, but nothing calls `record_usage` on this path).

### [INTG-01] At-rest integrity spot-check: PASSES on the core; residual anomalies are all honestly gated
- Domain: Integrity at rest · Severity: S4 (positive finding with caveats) · Priority: —
- Evidence (statistics, live 2026-07-20):
  - **Articulation:** 15/15 large caps (RELIANCE, TCS, INFY, ITC, MARUTI, SBIN, HDFCBANK,
    TITAN, BAJFINANCE, HINDUNILVR, LT, SUNPHARMA, ICICIBANK, KOTAKBANK, BHARTIARTL) —
    list-endpoint PAT/revenue/equity == latest statement-year values to the decimal (0.000
    relative error); accounting identities pass (revenue ≥ PBT, net_worth ≤ total_assets) 15/15.
  - **ROE field == PAT/equity** exactly for all 1,001 rows (computed from the same fields —
    consistent by construction, no independent drift possible).
  - **Impossible values:** 0 negative share counts; 12 names ROE > 60% — all real high-payout
    franchises (PGHH 1.14, COLPAL 0.84, NESTLEIND 0.68, GILLETTE 0.69…) except SPARC (1.16, loss-base
    artifact); 3 names ROE < −1 (KWIL, VIPIND, SWANDEF) consistent with distressed equity; 0 names
    with P/B < 0.2 & ROE > 10% (the stale-split trap signature is clean).
  - **Gates hold:** all 19 names with MoS > 2.0 print verdict LOW CONF; the 5 PE < 3 names
    (BOSCH-HCIL 1.41, ASHOKA 1.36, RELINFRA 0.89, PTC 2.55, APOLLO 2.92) are all LOW CONF —
    these are the DATA-01 mis-resolution cohort, gated but still wrong at rest.
  - **Cross-check endpoint:** 1,002 names, 1 alert (GUJGASLTD: no history + no snapshot —
    also an unresolved alias name), 0 divergence warnings; Dhan token valid (expires 15:19 UTC
    today, self-renewing).
- Caveat (out of my scope, cross-ref valuation audit VAL-01): KKCL and WEBELSOLAR print BUY at
  `medium` confidence with MoS 0.83/0.70 — the documented "BUY needs HIGH confidence" gate
  (HANDOFF.md §4) does not match prod behavior.

### [INTG-02] Placeholder/synthetic handling: correctly designed, correctly wired — no unflagged leak found
- Domain: Placeholder vs real · Severity: S4 (positive) · Priority: —
- Evidence: `app/assemble.py:45-79` sets `price=1.0` sentinel **plus** `synthetic_price` flag and
  synthesizes a flat series **plus** `synthetic_series` flag; `app/engines.py:505-508` refuses a
  MoS on a synthetic price (→ NO DATA), `:546-552` forces momentum neutral on a synthetic series;
  `app/data_quality.py:36-39` penalizes both (0.10 / 0.60) and `:74-75` hard-caps synthetic-series
  confidence at 0.79 (< high). Live: 0 of 1,001 list rows show the ₹1.0 sentinel; 33 NO DATA
  verdicts correspond to the flag path. The one historical leak (SENCO "BUY +197% high-conf" on a
  synthetic series) is the fix documented at data_quality.py:69-75 — verified present.

### [INTG-03] Corporate actions: single-adjustment doctrine is consistent; one stale docstring
- Domain: Corporate actions · Severity: S4 · Priority: P4
- Evidence: Dhan's vendor-adjusted series is served as-is (`app/history_routes.py:82-91`, with the
  double-adjustment lesson recorded: "CESC x10.45, COFORGE x5.07"); factor computation uses the
  same series without re-applying the ledger (`app/signals.py:50-51`); the ledger
  (`app/corporate_actions.py`) is applied only to dividend scaling in portfolio math
  (`app/portfolio_routes.py:287`) — correct; ingester's bonus-ratio parser refuses to guess
  (`_bonus_ratio_factor` returns None → event skipped + logged, `indianapi_ingester.py:333-347,
  393-396`); V-spike bad ticks filtered on read (`app/price_hygiene.py`). **Stale doc:**
  `app/dhan/backfill.py:7-8` still claims "the /history endpoint already back-adjusts on read
  (Phase A)" — it deliberately no longer does; correct the comment before it misleads a future
  edit into re-adjusting.

### [INTG-04] Freshness & lineage: prices have full lineage; statements have source but no timestamps
- Domain: Freshness/lineage · Severity: S3 · Likelihood: — · Effort: S · Priority: P3
- Evidence: MarketSnapshot carries `as_of` stamped on every update
  (`indianapi_ingester.py:262-272` — the fix for the frozen-as_of bug is in place);
  HistoricalPrice is dated; CompanyInsight has `updated_at`; `/api/health` exposes
  `errors_1h/scheduler_beat_min/price_age_days`. **Gap:** `HistoricalFinancial` and
  `FinancialFact` carry `source` but no `created_at/updated_at` (`app/models.py:85-105, 70-81`) —
  you cannot tell when a statement row was last refreshed, so "fundamentals staleness" is
  unmeasurable at rest (compounds DATA-06). Recommended: add an `updated_at` column
  (additive migration) and surface `fundamentals_age_days` in the admin coverage summary.

---

## Prior-issue status (data-related items from AUDIT_2026-06.md / HANDOFF.md)

| Prior issue | Status | Evidence |
|---|---|---|
| C1 — DCF on yfinance assumptions, flat beta 1.0 | **Fixed** | `assemble.effective_assumptions` derives from stored statements (`assemble.py:136-174`); beta regression wired into the scheduler (`scheduler.py:86-96`, DAT-04 remediation) |
| C2 — company page vs screener disagree | **Fixed** | July audit verified detail == list to the decimal; single Valuation store |
| C3 — TRIM verdict ships | **Fixed (backend)** | only a normalization shim remains (`main.py:363`) |
| C4 — consensus overlay defeats independence | **Fixed** | analyst block is separate/labelled in list rows (verified live) |
| C5 — compute_valuations dead (`models.Valuation` missing) | **Fixed** | Valuation table exists (`data_integrity.py` queries it); scheduler recomputes on every refresh |
| C6 — HDFC Bank mis-templated; ROE 68–92%; bank P&L nearly empty | **Partial** | template BANK + net-worth ROE fixed (live roe 12.97%); **bank P&L still pat/pbt/tax-only for all 5 years on every major bank — see DATA-03** |
| C7 — taxonomy too coarse for multiples | **Fixed** | `valuation_sector` live on every row (AUTO/METAL/CEMENT/UTILITIES… distinct from template) |
| §4 — `bse_filing_date` NULL (fractional-second timestamps) | **Open (dormant)** | BSE announcement ingestion is blocked by anti-bot upstream anyway (memory: AnnGetData "No Record Found!"); docs freshness now rides IndianAPI `/documents` |
| HANDOFF — YESBANK vendor statement gap | **Open** | live: 5 years pbt/pat only, 1 year with top line (same class as DATA-03) |
| HANDOFF — ~170 MANUFACTURING "Unknown" stubs needing backfill | **Mostly fixed** | now 13 Unknown sectors / 33 NO DATA (daily `run_coverage_backfill` since 17 Jul); residual = alias/resolution failures (Coverage bucket C) |
| Dhan UTC-shifted-date poisoning (Jul 2026) | **Fixed** | IST converter (`dhan/client.py:145-165`), repair flag executed, `repair_shifted_histories` retained |
| LICHSGFIN quarterly-lines-as-annual | **Fixed** | annual-scale + identity guards in `_financial_pl_supplement` (`indianapi_ingester.py:849-894`) |
| Frozen MarketSnapshot.as_of (freshness unknowable) | **Fixed** | as_of stamped on every update (`indianapi_ingester.py:262-272`) |
| SENCO synthetic-series high-conf BUY | **Fixed** | 0.79 confidence cap (`data_quality.py:69-75`) |
| July quota exhaustion → snapshot-first profiles | **Fixed / guard weakened** | snapshot-first live (`profile_routes.py`); but the budget meter under-counts (DATA-04) |

---

## 10-line summary

1. Coverage headline: the "243/1001 full P&L" figure is stale — measured today ~87% of 1,001 names carry 5 full P&L years (~90% ≥3); the daily backfill largely worked.
2. The residual gap is 4 attributable buckets: 34 banks with pat/pbt/tax-only P&L (supplement latest-year-only + silent guard no-op), 11 insurers with zero statements, 33 NO-DATA stubs (6 with aliases that demonstrably failed), and a mis-resolved-identity cohort.
3. Worst pipeline defect (DATA-01, S1): name-based `/stock` resolution is never identity-checked — VAML and VISL hold byte-identical wrong balance sheets in prod, and the RAJESHEXPO/BOSCH-HCIL/APOLLO forced-re-ingest cohort loops daily without converging.
4. Second S1 (DATA-02): every re-ingest purges all statement history before validating the new payload; a price-only vendor response permanently wipes a 7-year history (observed: SBILIFE 0 years, `has_data:false`).
5. DATA-03 (S2): the "financial-sector edge" is still unpopulated — HDFCBANK/ICICIBANK/SBIN/KOTAKBANK have zero years of interest income/NII/opex/provisions live (June C6 only half-fixed).
6. DATA-04 (S2): vendor-quota metering counts only `_get` calls — `_get_safe` insight calls (~9-14/name) and direct `ingest_company` paths are un-metered, so the budget guard governs on ~10-15% of true spend.
7. Scheduler design is otherwise sound: idempotent upserts under real unique constraints, per-name rollback isolation, heartbeat + price_age_days liveness — but job failures are log-only (except backups) and fundamentals staleness has no health signal (DATA-06, INTG-04).
8. Integrity at rest PASSES the spot-checks: 15/15 large-cap articulation exact, identities hold, 0 impossible share counts, all 19 MoS>2 names and all 5 PE<3 names correctly gated LOW CONF; cross-check shows 1 alert / 0 divergences and a healthy Dhan token.
9. Placeholder handling (synthetic_price/series → NO-DATA / neutral momentum / 0.79 conf cap) is correctly wired with no unflagged leak found; corporate-action single-adjustment doctrine is consistent (one stale docstring in dhan/backfill.py).
10. Counts: S1 ×2 (DATA-01, DATA-02) · S2 ×2 (DATA-03, DATA-04) · S3 ×5 (DATA-05..08, INTG-04) · S4/positive ×4 (DATA-09, INTG-01..03); top three fixes by leverage: identity check on resolution, parse-before-purge, per-year lender supplement.
