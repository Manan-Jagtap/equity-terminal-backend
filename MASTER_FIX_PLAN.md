# EquityVerdict — Master Fix Plan (2026-07-20)

The single authoritative, deduplicated, dependency-sequenced fix queue,
consolidating every audit lens shipped to this repo:
`VALUATION_AUDIT_2026-07/` (VAL-01..11) · `VALUATION_GROUNDTRUTH_2026-07/`
(CORR-1..5 + groundtruth.csv + calibration_targets.csv) ·
`VERDICT_EXPLAINABILITY_2026-07/` (EXPL-01..03, ARC-02 spec, thesis_corpus.csv)
· `FM_ENGINE_AUDIT_2026-07/` (FM-01..09) · `PLATFORM_AUDIT_2026-07/`
(DATA/INTG/ENG/OPS/SCALE/TEST/INST + ARCH) · `SECURITY_AUDIT.md` (SEC-*,
shipped July — parallel track). **This document changes no code; it is the
handoff to execution (Opus 4.8), worked top-to-bottom, one row at a time.**

---

## Executive summary

**28 consolidated fixes** from ~75 source findings across 5 independent audit
lenses; every source ID maps to exactly one row (coverage table §4 — nothing
dropped, nothing double-counted). By wave: W0 ×2 · W1 ×6 · W2 ×9 · W3 ×1 ·
W4 ×6 · W5 ×4, plus 2 parallel tracks. By severity of the underlying worst
finding: S1 ×7 · S2 ×13 · S3 ×8.

**The converged high-confidence set** (multiple independent lenses agree the
engine is wrong — fix first within their wave): the two-method condemned
names **REDINGTON, PATELENG (bullish) · JAMNAAUTO, HUDCO, HYUNDAI, PTC
(bearish)**; the three-lens exit-multiple inflation cohort (~124 confident
calls incl. most published BUYs); the AUBANK/PATELENG ledger specimens
(valuation + explainability + FM's own frozen ledger).

**Date-gated:** FIX-07 (IndianAPI quota metering + budget re-size) must land
**before ~11 Aug 2026** regardless of rank.

**Start here → FIX-01** (the ENG-01 import line + job restructure: un-freezes
the public track-record ledger) and **FIX-02** (deploy the already-committed
VAL-01/03 gate fixes — code is sitting in `18a5125` with the image built
while AWL still shows "BUY +91%" in prod).

**Non-negotiable guardrails carried into every row:** honest abstention
preserved end-to-end — no fix manufactures confidence on thin data; gate-
loosening is never a remedy (FIX-10 converts abstentions with zero loosening);
CORR-5 re-banding runs LAST (Wave 3, after all level fixes); the ledger gap is
annotated, NEVER backfilled; the thesis corpus stays internal/compliance-gated
and the ARC-02 revival is a rules-based composer (no LLM) behind a default-off
flag until SEBI-RA clears; security executes in its own Opus session.

**Regression safety for the whole program:** `calibration_targets.csv`
(319 Tier-A corrections) is the regression suite; the 197 groundtruth "Agree"
rows + `thesis_corpus.csv` classes are do-not-break fixtures; both parity
harnesses (60/60, 48/48) re-run on every engine-math change.

---

## §2 The master queue (top-to-bottom is a safe execution order)

### Wave 0 — stop the live/public bleeding
| ID | Title | Sev | Eff | Live impact | Sources (merged) | Lenses | Depends | Exec | What to do | Done-check |
|---|---|---|---|---|---|---|---|---|---|---|
| **FIX-01** | Un-freeze the public ledger: macro import + job restructure + gap annotation | S1 | S | Public track record silent since 15 Jul (5 days lost) | ENG-01, FM-01, FM-08 | 2 | — | Opus | Import `macro_data`; restructure `snapshot_evidence` so partial completion is impossible (macro failure → `macro_stale` flag, ledger still appends, job failure → errors_1h); publish gap annotation 16–20 Jul. **Never backfill.** | Ledger appends tonight; staged macro-kill still appends with flag; gap note visible |
| **FIX-02** | Deploy the committed VAL-01/VAL-03 gate fixes | S1 | XS | 27 gate-hole confident calls incl. AWL "BUY +91%" still public | VAL-01, VAL-03 (code in `18a5125`; image built) | 3 | — | Human (deploy) + Opus (verify) | Owner runs ECR push + SSM cutover; trigger recompute | `AWL` reads LOW CONF live; batch/intraday verdict-drift test passes |

### Wave 1 — data & pipeline integrity + kill silent failure
| ID | Title | Sev | Eff | Live impact | Sources | Lenses | Depends | Exec | What to do | Done-check |
|---|---|---|---|---|---|---|---|---|---|---|
| **FIX-03** | Post-resolution identity check + quarantine/re-ingest the contaminated cohort | S1 | S | VAML/VISL wrong statements at rest; daily non-converging loop | DATA-01 (+6 groundtruth data-defect rows) | 2 | — | Opus | Verify vendor identity (name/ISIN match) after `/stock` resolution; quarantine + re-ingest VAML, VISL, RAJESHEXPO, BOSCH-HCIL, APOLLO | Cohort statements differ & articulate; loop converges |
| **FIX-04** | Parse-and-validate BEFORE purge; restore SBILIFE | S1 | S | Any re-ingest can destroy a 7-yr history; SBILIFE wiped live | DATA-02 | 1 | — | Opus | Stage new payload → validate (years, magnitudes) → swap atomically; re-ingest SBILIFE | SBILIFE `has_data:true` with full years; simulated partial payload leaves history intact |
| **FIX-05** | Alert on the signals that already exist | S2 | XS | Silent data rot pages nobody (mechanism behind FIX-01's 5-day silence) | OPS-01, DATA-06, OPS-06 | 3 | — | Opus | uptime.yml: alert on `scheduler_beat_min` > 120 and `price_age_days` > 3; weekly integrity-sweep failures → same channel | Staged stale-price fires an alert |
| **FIX-06** | Swallowed-exception triage on write paths | S2 | M | 149 sites; real failures indistinguishable from noise | ARCH-03 | 3 | FIX-05 | Opus | Every `except:pass` on a write path/scheduled job → `error_log` with job tag (feeds errors_1h) | Induced job failure visible in /api/health; write-path silent count ≈ 0 |
| **FIX-07** | **DATE-GATED ≤ 11 Aug:** true vendor-quota metering + budget re-size + burn plan | S2 | S | Budget guard governs on ~10–15% of real spend; plan downgrades ~11 Aug | DATA-04, SCALE-04 | 1 | — | Opus + Human (plan choice) | Meter `_get_safe`/direct-ingest calls; recompute steady-state burn; re-size `API_BUDGET`; owner picks post-downgrade plan | Metered count ≈ vendor dashboard; budget env matches plan before 11 Aug |
| **FIX-08** | Per-year lender P&L supplement (the "financial edge") | S2 | M | 34 big banks have pat/pbt/tax-only P&L; blocks bank/fee models | DATA-03 | 2 | FIX-03, FIX-04 | Opus | Extend supplement to all years; fix silent no-op on the large banks | HDFCBANK/ICICIBANK/SBIN carry NII/opex/provisions for ≥5y |

### Wave 2 — valuation LEVEL fixes (the moat; condemned names first)
| ID | Title | Sev | Eff | Live impact | Sources | Lenses | Depends | Exec | What to do | Done-check |
|---|---|---|---|---|---|---|---|---|---|---|
| **FIX-09** | Margin-based DISTRIBUTION-economics override (AWL-class) | S2 | S | ~56 thin-margin names on rich staples multiples; incl. condemned REDINGTON, PATELENG | CORR-2, VAL-01(family), EXPL(condemned set) | 3 | FIX-02 | Opus | Through-cycle npm < 4% + revenue > ₹5k cr in CONSUMER/_DISC/MANUFACTURING → DISTRIBUTION params; guard: never re-bucket already-cyclical sectors | Condemned bullish pair lands in target bands; no METAL/ENERGY re-bucketed; parity 60/60+48/48 |
| **FIX-10** | Corroboration-aware lender gate + preset re-seed + segment-EBITDA basis | S2 | S | Converts PSU-lender abstentions (PFC/RECLTD/LICHSGFIN…) with ZERO gate loosening; ITC stale +113% | CORR-3, VAL-08, groundtruth wrongly-abstained | 2 | FIX-02 | Opus | Lender mos ≥ 0.80 keeps its call iff `gordon_pb_value` ≥ 1.25× price, else LOW CONF as today; re-seed ITC/RELIANCE SOTP + insurer EV from FY26 filings; `segment_sotp` multiples onto EBITDA basis; preset staleness warning after 2 quarters | PSU-lender cohort issues banded calls; ITC ≈ independent +6% zone; presets stamped |
| **FIX-11** | Exit-multiple deflation (the single highest-leverage level change) | S1 | M | ~124 confident calls incl. most published BUYs read rich | CORR-1, VAL-01(residue) | 3 | FIX-02, FIX-09 | Opus | Cross-check clamp `[0.5,2.2]→[0.6,1.6]`; Exit weight .30→.20 (DCF .55→.65); re-base CONSUMER 42→34, CONSUMER_DISC 38→30, CAPITAL_GOODS 32→27, DEFENCE 34→28. **Parity: mirror engine.js + regen fixtures.** | 124-cohort inside `calibration_targets` bands; 197 Agree rows unchanged; 22 corroborated BUYs survive; parity green |
| **FIX-12** | type↔sector reconciliation (category-error names) | S2 | S | 12 brokers/holdcos run FCFF mechanics (ARSSBL +176% gated) | VAL-06 | 2 | — | Opus | Reconcile at classification; fee-financials → VAL-05 path, lenders → RI, holdcos → NAV/SOTP | Class-mismatch list in matrix.csv empties |
| **FIX-13** | Confidence earns its name: dispersion + sensitivity + TV-share legs (+ interface fields) | S2 | M | BUYs shipped at 15× dispersion/81% swing with HIGH conf; 99 "should-abstain" names | VAL-04, VAL-07, EXPL(should-abstain) | 3 | FIX-11 | Opus | conviction = f(data tier, computed-method dispersion, ±1% swing, TV%>85 cap); expose `{data_tier, method_dispersion, sensitivity_swing, tv_share, gate_state}` on the Valuation row/API (the FM contract) | No BUY above 2.5× dispersion; fields live on /api/companies |
| **FIX-14** | Gate-filtered MoS into the Alpha value factor | S2 | S | Ideas rank #1 = KISSHT (LOW CONF, +450% MoS); 12 of top-20 are no-calls | EXPL-01 | 2 | FIX-02 | Opus | factors.py value leg consumes gate-state-filtered MoS (LOW CONF/NO DATA names get neutral value credit) | Alpha top-20 contains no LOW CONF names with extreme-MoS-driven rank |
| **FIX-15** | Surface "engines disagree" (verdict × Alpha reconciliation) | S3 | S | 7 names told opposite stories (LT, VEDL, POWERGRID…) | EXPL-02, (ENG-02 lens) | 2 | FIX-14 | Opus | API + UI state when verdict is bearish and Alpha ≥ 70 (or inverse): explicit chip + one-line explanation | The 7 names render the disagree state |
| **FIX-16** | Missing archetype models: young/loss-maker (survival-adjusted) + fee-annuity | S2 | L | 148 permanent abstentions convert honestly (116 + 32) | VAL-05, groundtruth wrongly-abstained(fee) | 3 | FIX-08, FIX-12 | Opus | Revenue→margin→sales-to-capital with survival weighting (young); earnings-power on own 10y P/E band (fee); eligibility earned — names failing input requirements keep abstaining | ≥half the cohort graduates to banded MEDIUM-conf calls; ZERO forced calls on thin data |
| **FIX-17** | Long-duration compounder archetype (P0-3) | S1 | L | 98 gated quality names + DMART/BAJFINANCE-class live verdicts | VAL-02, CORR-4, VAL-09(partial: fin terminal-ROE evidence path) | 3 | FIX-11, FIX-13 | Opus | Evidence-earned 3-stage (10y ROIC/ROE ≥1.5×Ke persistence → company-evidenced stage-2 length); lender terminal-ROE may exceed forecast only on a 10y persistence test; per-name targets in calibration_targets | DMART/NESTLEIND/HUL/BAJFINANCE/HDFCBANK inside target bands; `LC_compounder_understated` < 30 via modeling, not gate removal; parity green |

### Wave 3 — valuation calibration (ONLY after Wave 2)
| **FIX-18** | Verdict re-banding + ladder symmetry | S2 | S | 135 threshold-difference names; 143 "priced-for-collapse" AVOIDs; bearish skew | CORR-5, VAL-10 | 3 | FIX-09..17 ALL | Opus | Re-run distribution on the fixed engine; HOLD band ±12%; BUY requires ≥2 corroborating legs; AVOID gains a quality/extremity condition symmetrical to BUY's | Verdict distribution defensible (BUY median MoS ~+20-30%, AVOID ~−35-45%); groundtruth agreement ≥ 75% |

### Wave 4 — fund-manager engine (after the valuation contract exists)
| ID | Title | Sev | Eff | Sources | Lenses | Depends | What to do → Done-check |
|---|---|---|---|---|---|---|---|
| **FIX-19** | FM consumes the full valuation contract + hard ledger eligibility | S1 | M | FM-02, (crosscheck AUBANK, EXPL PATELENG) | 3 | FIX-13 | `gate_state ≠ CLEAN` or `data_tier ≠ full` → ineligible for public ledger, conviction ≤ 60; dispersion > 2.5 → model vote ×0.5 → AUBANK/PATELENG-class can never top the ledger |
| **FIX-20** | Conviction-aware sizing (× vol × ADV × regime) | S2 | M | FM-03 | 2 | FIX-19 | Tranche = base × conviction tier × inverse-vol × liquidity cap (≤5× median daily value) → sizes differ by conviction/vol in the action feed |
| **FIX-21** | Portfolio-level risk controls | S2 | M | FM-04 | 1 | FIX-20 | Sector ≤25%, single-name ≤8% (trim prompt at 10% not 30%), correlation guard (avg pairwise > 0.75 rejects), portfolio σ band 12–18% + DD alert −12% → nightly checks surface in PM note |
| **FIX-22** | Self-trust un-inert + turnover discipline + TRI benchmark | S3 | S | FM-05, FM-06, FM-07, ENG-07, ENG-09 | 2 | FIX-19 | Widen trust horizons + surface coverage; action hysteresis (10 sessions / 15-pt move) + 30bps cost line; grade ledger vs NIFTY TRI → `model_trust_sectors > 0`; churn test |
| **FIX-23** | Track-record honesty tail | S3 | S | ENG-04, ENG-05, ENG-06 | 1 | FIX-01 | Delisted/price-less open calls excluded-with-note (not frozen 0%); calibrate() treats HOLD as directionless; factor track uses uniform horizons → backtest suite green |
| **FIX-24** | Alpha factor hygiene | S3 | S | ENG-02, ENG-03, ENG-12..14, ENG-16 | 1 | FIX-14 | Rename/replace consensus-upside "growth" leg; bound catalyst window; Sloan flag on signed accruals; risk-analytics inputs (one XIRR basis — buy_date; correlation-aware vol note) → factor docs match math; two XIRRs converge |

### Wave 5 — release safety, scale, cleanup (make future fixes cheap)
| ID | Title | Sev | Eff | Sources | Depends | What to do → Done-check |
|---|---|---|---|---|---|---|
| **FIX-25** | Release safety: rebuildable prod + immutable tags + CI-gated deploys + real migrations | S2 | M | OPS-02, OPS-03, OPS-04, ARCH-01, TEST-02 | — | Commit Caddyfile/user-data/cutover; tag images by SHA + previous-tag rollback; deploy from CI artifact + post-deploy smoke; `alembic upgrade head` fail-closed at boot → staged bad migration cannot reach prod; rollback drill documented |
| **FIX-26** | Test the reality: Postgres CI + ingester fixtures + contract test | S2 | M | TEST-01, TEST-03, TEST-04, TEST-05 | FIX-25 | CI job on Postgres; ingester regression fixtures (incl. partial/garbled payloads — locks FIX-03/04); one FE↔BE contract test; lint gating → suite fails on the DATA-01/02 bug classes |
| **FIX-27** | Scale headroom quick wins | S2 | S | SCALE-01, SCALE-02, SCALE-03, SCALE-05, OPS-08, OPS-05 | — | Pool sizing + pre-ping; stampede locks + bound `_all_latest_facts`; O(1) health probe; disk prune doc; watch the Vercel FE; quarterly restore drill → 10× load test passes on staging profile |
| **FIX-28** | Hygiene & product tail | S3 | M | ARCH-02, ARCH-04, ARCH-05, DATA-05, DATA-07, DATA-08, DATA-09, INTG-04, VAL-09(rest), ENG-08, ENG-10, ENG-11, ENG-13, ENG-15(spec'd in FIX-20), ENG-17, ENG-19, OPS-07, INST-01, INST-02, ARC-02 | FIX-25 | Delete probe/dump/.bak litter (+the dangerous manual-figures path); KV envelope; confirm+prune dead endpoints; FY labels; ingest magnitude floors; statement timestamps; LTCG boundary + exemption double-count; sentiment negation/expiry; DEFENCE terminal-g; self-owned analytics + feedback channel (DPDP-fit); ARC-02 rules-based thesis composer behind default-off flag (compliance-gated) → each with its named check; litter gone from image |

---

## §3 Dependency map (hard must-precede edges; acyclic)
```
FIX-02 (gates live) ─► FIX-09, FIX-10, FIX-11, FIX-14
FIX-03/04 (data trustworthy) ─► FIX-08 ─► FIX-16
FIX-05 ─► FIX-06        (alert channel before rewiring silence into it)
FIX-09 ─► FIX-11        (reclassified names before re-basing their multiples)
FIX-11 ─► FIX-13 ─► FIX-17          (levels → confidence/contract → archetype)
FIX-12 ─► FIX-16        (routing before the models that need it)
FIX-09..17 ALL ─► FIX-18 (CORR-5 LAST — never pre-tune thresholds)
FIX-13 ─► FIX-19 ─► FIX-20 ─► FIX-21   (valuation contract → FM consumes → sizing → risk)
FIX-14 ─► FIX-15, FIX-24
FIX-01 ─► FIX-23        (ledger alive before grading fixes)
FIX-25 ─► FIX-26, FIX-28
Date gate: FIX-07 ≤ 2026-08-11 (independent of rank)
```

## §4 Coverage check — every source ID → exactly one FIX
| Source → FIX | | |
|---|---|---|
| VAL-01 → FIX-02 (shipped) + residue FIX-11 | VAL-02 → FIX-17 | VAL-03 → FIX-02 |
| VAL-04 → FIX-13 | VAL-05 → FIX-16 | VAL-06 → FIX-12 |
| VAL-07 → FIX-13 | VAL-08 → FIX-10 | VAL-09 → FIX-17 (fin-ROE path) + FIX-28 (rest) |
| VAL-10 → FIX-18 | VAL-11 → no-fix (verified strengths) | CORR-1 → FIX-11 |
| CORR-2 → FIX-09 | CORR-3 → FIX-10 | CORR-4 → FIX-17 |
| CORR-5 → FIX-18 | EXPL-01 → FIX-14 | EXPL-02 → FIX-15 |
| EXPL-03 → no-fix (pass) | ARC-02 spec → FIX-28 | FM-01 → FIX-01 |
| FM-02 → FIX-19 | FM-03 → FIX-20 | FM-04 → FIX-21 |
| FM-05 → FIX-22 | FM-06 → FIX-22 | FM-07 → FIX-22 |
| FM-08 → FIX-01 | FM-09 → no-fix (strengths) | ENG-01 → FIX-01 |
| ENG-02 → FIX-24 | ENG-03 → FIX-24 | ENG-04 → FIX-23 |
| ENG-05 → FIX-23 | ENG-06 → FIX-23 | ENG-07 → FIX-22 |
| ENG-08 → FIX-24 | ENG-09 → FIX-22 | ENG-10 → FIX-28 |
| ENG-11 → FIX-28 | ENG-12 → FIX-24 | ENG-13 → FIX-28 |
| ENG-14 → FIX-24 | ENG-15 → FIX-20 | ENG-16 → FIX-24 |
| ENG-17 → FIX-28 | ENG-18 → no-fix (honest) | ENG-19 → FIX-28 |
| DATA-01 → FIX-03 | DATA-02 → FIX-04 | DATA-03 → FIX-08 |
| DATA-04 → FIX-07 | DATA-05 → FIX-28 | DATA-06 → FIX-05 |
| DATA-07 → FIX-28 | DATA-08 → FIX-28 | DATA-09 → FIX-28 (note) |
| INTG-01/02/03 → no-fix (passes) | INTG-04 → FIX-28 | OPS-01 → FIX-05 |
| OPS-02/03/04 → FIX-25 | OPS-05 → FIX-27 | OPS-06 → FIX-05 |
| OPS-07 → FIX-28 | OPS-08 → FIX-27 | SCALE-01/02/03/05 → FIX-27 |
| SCALE-04 → FIX-07 | TEST-01/03/04/05 → FIX-26 | TEST-02 → FIX-25 |
| INST-01/02 → FIX-28 | INST-03 → no-fix (sound) | ARCH-01 → FIX-25 |
| ARCH-02 → FIX-28 | ARCH-03 → FIX-06 | ARCH-04/05 → FIX-28 |

## §5 Parallel tracks (do not block the code queue)
**Security (Opus-security session):** SEC-01..11 from `SECURITY_AUDIT.md`
shipped in the July fix pass (token revocation, CORS fail-closed, scrypt
backups, SSL verify-full option…). Remaining: a fresh exploitability
re-audit post-redesign + post-fix-waves — schedule as its own Opus session;
this plan is incomplete until its findings merge here.
**Compliance (Human/legal — decisions, not commits):** SEBI RA registration
via BSE RAASB (NISM-XV cert already held, valid to Nov 2027) — gates
charging AND the ARC-02 thesis tab; vendor data-licensing review (IndianAPI/
Dhan redistribution terms); DPDP posture maintained (India-resident,
self-owned telemetry only). Mini-sequence: RA application → licensing
letters → then FIX-28's ARC-02 flag may flip on.
