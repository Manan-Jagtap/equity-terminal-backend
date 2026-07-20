# Fund-Manager Engine — Deep Audit & Design Review (2026-07-20)

Read-only review of `app/manager_engine.py` (1,177 lines) + its scheduler jobs,
ledger, and prod behavior, judged as a risk officer would judge a junior
quant's strategy. No code changed. Companions: valuation audit (input side),
ground-truth cross-check, explainability probe, platform audit (ENG-01).

**What this engine actually is:** an *advisory action engine* — it scores an
ADD/TRIM conviction per name against the user's own book and publishes a
nightly top-15 "ADD CANDIDATE" ledger. It is NOT an autonomous portfolio
constructor (no target weights, no benchmark, no optimizer). Several classic
construction critiques therefore land as "missing by design-stage," and the
target spec below formalizes what a real construction layer needs.

---

## ENG-01 impact assessment (quantified)

**Mechanics** (confirmed in code + prod): `snapshot_evidence` (line 1123)
runs `build_evidence` and WRITES it (1128) **before** calling `macro_regime`
(1129), which throws NameError at the un-guarded `macro_data.macro_forecast`
in its return statement (1039; the two other `macro_data` uses at 952/984 are
inside try/except and merely nulled their legs). The exception unwinds before
the ledger block (1132+), and the scheduler swallows it nightly.

**Live state (prod, 20 Jul):** `evidence_as_of 2026-07-20T11:16Z` — per-name
evidence IS fresh daily. Macro blob frozen at its last good write (~15 Jul).
`engine-ledger` last row **2026-07-15** → **5 calendar days (3–4 market days)
of the public track record are permanently missing.**

**Corrupted or stale?** Stale, not corrupted: conviction math consumed the
frozen macro's regime label ("neutral" at freeze). Exposure: had the tape
turned risk-off in the window, new-entry tranches would NOT have halved —
risk understated, though breadth (58% above 200-DMA) stayed benign, so the
practical harm this window ≈ the ledger gap itself.

**Recovery:** (1) one-line import fix (already in the sequenced fix pass);
(2) ledger resumes next nightly run; (3) the missed days must **NOT** be
backfilled — the ledger's own doctrine is "recorded daily, never backfilled"
(portfolio_routes.py:55) and hindsight rows would poison it; instead publish
an explicit gap annotation (dates + cause) in the ledger view; (4) regression:
the VAL-03-style rule — any exception between evidence and ledger must fail
the job LOUDLY into errors_1h (cross-ref OPS-01/ARCH-03).

---

## Findings

### FM-01 One un-guarded macro call froze the public ledger for 5 days, silently
- S1 / happened / XS · Location: manager_engine.py:1039 (+ scheduler swallow)
- Root cause: `macro_data` never imported at module level; 2 of 3 uses are
  try-wrapped, the third is in the return expression. The job half-completes
  by construction (evidence write precedes the fragile call).
- Fix: the import (one line) + restructure `snapshot_evidence` so partial
  completion is impossible (macro failure → explicit degraded-macro flag in
  the blob, ledger still appends with `macro_stale=true`), + gap annotation.
- Verification: kill macro_data deliberately in staging → ledger still
  appends, /api/health errors_1h increments, macro carries stale flag.

### FM-02 Input contract only half-honors valuation uncertainty — suspect-model names still reach top-conviction ideas
- S1 / High / S — the brief's central question, answered precisely:
- **What works:** LOW confidence → SUSPECT (triangulate, :238); suspect →
  model vote set aside + conviction capped (:410 "a name whose ONLY case is a
  suspect model never earns high conviction"); ledger gates on quality ≥55,
  no red flags, momentum. A thin-data no-call name does NOT get full weight.
- **What fails:** the contract fields the valuation audit specified —
  `data_tier, method_dispersion, sensitivity_swing, tv_share, gate_state` —
  are not consumed at all; consensus+band witnesses can outvote a broken
  model leg with no dispersion awareness. **Evidence from the frozen ledger's
  own final page:** AUBANK "ADD CANDIDATE, conviction 86" while the valuation
  verdict is AVOID with 16.3× method dispersion; **PATELENG conviction 83 —
  the name BOTH independent probes (cross-check + explainability) condemn.**
- Fix: consume the full interface contract; dispersion above tolerance or
  gate_state ≠ CLEAN caps conviction ≤ 60 and excludes from the public
  ledger; add "engines disagree" surfacing (EXPL-02).
- Verification: PATELENG/AUBANK-class names cannot enter the ledger top-15.

### FM-03 Sizing is conviction-blind
- S2 / High / M · Location: tranche logic (flat 3% starter / 1.5% half)
- A conviction-87 large-cap and a conviction-62 micro-cap get identical
  tranches; only the regime flag halves. No volatility scaling, no
  conviction scaling, no Kelly-fraction awareness, no liquidity/ADV input.
- Fix (target spec §sizing): tranche = base × conviction-tier multiplier
  (0.5×/1×/1.5×) × inverse-vol scalar (252d σ bucketed) × liquidity cap
  (position ≤ x days of ADV), hard cap per name.

### FM-04 Risk controls: assessed by name
- S2 / High / M — Present: single-name TRIM prompt at >30% of book (:442 —
  institutionally loose; 8–10% is the norm), pledge/red-flag exclusions,
  headline screen, regime half-tranche. **Absent: sector concentration
  limits · correlation awareness (nothing prevents 15 correlated financials)
  · portfolio drawdown/vol targeting · liquidity constraints.** The risk
  layer protects against bad *names*, not bad *portfolios*.

### FM-05 No turnover or transaction-cost consciousness (S3/M): actions have
no cost model, no churn budget, no minimum-holding hysteresis; a user
following daily could be churned. Fix: action hysteresis (no flip within N
days without ≥15-point conviction move) + cost line in every action.

### FM-06 Model self-trust is inert in production (S3/XS): prod reports
`model_trust_sectors: 0` — `model_trust_by_sector` (min 8 calls/sector at
6-month horizon) has no qualifying sectors, so every model vote silently uses
the 0.6 fallback. The advertised "weighted by realized hit-rate" feature is
not operating; surface trust coverage in engine-status and widen horizons
(ENG-07 fixes fold in here).

### FM-07 No benchmark or active-weight concept (S3/M): actions are absolute;
no NIFTY-relative framing, so the user can't see active bets. Target spec
introduces a shadow benchmark for the ledger's grading (TRI, per ENG-09).

### FM-08 Robustness is genuinely good EXCEPT the ENG-01 class (S3): missing
legs re-weight gracefully (verified across evidence builder's try/except
lattice); empty eligible set → empty ledger day (safe); extreme values
bounded by clamps. The failure mode is *partial job completion* — fixed by
FM-01's restructure. The 149-swallowed-exceptions hygiene (ARCH-03) is the
systemic enabler.

### FM-09 What is genuinely strong (keep): evidence triangulation design;
democratic overrule; calibration honesty (walk-forward, shrink-to-prior,
weights visibly loaded: momentum 0.20 vs prior 0.16 in prod); pledge as a
first-class red flag; news red-flag screen fail-silent-never-fabricates;
"educational, not advice" doctrine consistently embedded.

---

## Target design specification (Opus-buildable)

**Input contract (per name):** `{intrinsic, mos, verdict, confidence,
data_tier, archetype, method_dispersion, sensitivity_swing, tv_share,
gate_state}` from the valuation engine (VAL audit interface) + evidence blob.
Hard rules: `gate_state ≠ CLEAN` or `data_tier ≠ full` → ineligible for the
public ledger and conviction ≤ 60; dispersion > 2.5 → model vote weight ×0.5.
**Honest abstention is preserved: thin-data names cannot enter portfolios.**

**Universe & eligibility:** liquidity floor (median 60d traded value ≥ ₹2 cr
for any sized idea; below → "research-only" label), red-flag/pledge
exclusions as today.

**Construction (formalize as conviction-tilted equal-risk):** rank by
conviction; target book of 15–25 names; weights = inverse-vol × conviction
tier, normalized; caps: single name ≤ 8% (hard 10%), sector ≤ 25%, top-5
names ≤ 35%; correlation guard: reject an add if avg 1y pairwise correlation
with existing book > 0.75 unless replacing a correlated name. Shadow
benchmark NIFTY 500 TRI; report active weights.

**Sizing:** tranche = 2% base × conviction multiplier (≥80: 1.5×, 65–79: 1×,
55–64: 0.5×) × vol scalar (σ_annual ≤20%: 1.2×, 20–35%: 1×, >35%: 0.7×) ×
regime factor (risk_off 0.5×) — capped by the ADV rule (position ≤ 5× median
daily traded value).

**Rebalancing/turnover:** weekly action cadence; hysteresis (no reversal
within 10 sessions absent a 15-point conviction move or red flag); assumed
cost 30 bps round-trip printed on every action; annual turnover budget
~100% flagged when breached.

**Risk overlay:** portfolio σ target band (12–18% annualized); breach →
trim highest-vol overweights first; max drawdown alert at −12% from peak
(alert, not forced sale — doctrine is advisory); concentration + correlation
checks run nightly with the evidence job and surface in the PM note.

**Degradation:** any missing subsystem (macro, flows, news, trust) sets an
explicit `degraded[]` list in the blob, shown in the PM note; the job never
partially completes; ledger always appends or the job FAILS into errors_1h.

**Ledger:** unchanged doctrine (daily, never backfilled, graded openly) +
gap annotations, TRI benchmark grading, and exclusion of any `gate_state ≠
CLEAN` name — the ledger is the product's honesty artifact; it must be the
strictest surface, not the loosest.

## Prior-work reconciliation (FM_ENGINE_CHECKLIST.md)
- Triangulation / suspect gates / P/B band / peer-relative P/E / forensics-
  feed-conviction / Beneish+Altman / pledge: **DONE — verified in code.**
- Model self-trust: **DONE-but-INERT in prod** (FM-06; trust sectors = 0).
- Macro regime block: **DONE, then REGRESSED** by ENG-01 (frozen since 15 Jul).
- Forward multiples: **NEEDS DATA — still open** (vendor lacks FY+1 EPS).
- Calibration (ICs, walk-forward): **DONE and honest** (weights measurably
  moved from priors in prod: momentum 0.16→0.20, quality 0.22→0.165).
- Not on the checklist but now required: the valuation interface contract
  (FM-02), conviction-aware sizing (FM-03), portfolio-level risk (FM-04).
