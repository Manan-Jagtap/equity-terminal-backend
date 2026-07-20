# Valuation Engine Audit — Full Universe (2026-07-20)

Read-only diagnosis of the valuation stack (FCFF DCF, Residual Income, SOTP/alt
models, confidence layer, verdict mapping) across **all 1,001 covered
companies**, run against live production output plus a 103-name deep-dive with
the engine's own code replayed locally. No engine code was changed in this pass.

Artifacts in this folder: `matrix.csv` (per-company Coverage & Quality Matrix,
all 1,001), `deep.csv` (103-name deep-dive analytics), `CALIBRATION.md`,
`ARCHETYPES.md`, `DOSSIERS.md`.

---

## Executive summary — the headline diagnosis

**The verdicts are not random, and the abstentions are not bugs. Every single
no-call in the universe is attributable to a designed, deterministic gate
(0 unexplained abstentions in 1,001 names). What the owner is feeling is the
VOLUME and PLACEMENT of those gates — which trace to one root disease: the
core DCF/RI produces intrinsic values that run systematically below market for
long-duration quality franchises, and the engine's honesty layer (correctly)
converts that model error into mass abstention instead of false AVOIDs.**

The numbers (full decomposition in `CALIBRATION.md`):

| Bucket | Count | Share |
|---|--:|--:|
| Abstentions (LOW CONF + NO DATA) | 330 | 33.0% |
| — of which: young/loss-maker, no model exists | 116 | 11.6% |
| — of which: high-ROE compounder the DCF prices >45% below market ("model admits it can't price quality") | 98 | 9.8% |
| — fee-financials (brokers/AMCs/exchanges), no model exists | 32 | 3.2% |
| — implausible-upside / lender-divergence / alt-divergence gates | 35 | 3.5% |
| — data-thin + insurers-without-EV + holdco + stubs | 49 | 4.9% |
| Issued verdicts skewed bearish: AVOID+REDUCE / issued | 454/671 | **68%** |
| Positive calls (BUY+ACCUMULATE) | 95 | 9.5% |

Calibration tells the same story from the other side: **BUY median MoS is
+52%, AVOID median MoS is −57%**. A well-calibrated engine's confident calls
cluster near ±15–30%; this one's surviving calls sit at extremes because the
intrinsic level itself is biased low — quality names pushed to −45%+ get gated
(98 names), leaving the BUY list dominated by names the model sees at huge
upside, half of which are exactly the thin-margin/cyclic names a generic model
over-values (see VAL-01).

**Is the engine credible as an analyst?** Verdict: *the honesty architecture is
genuinely good — the model underneath it is not yet earning it.* Model routing
(RI for financials, through-cycle for cyclicals, SOTP/P-EV/combined-ratio alt
models, gates instead of fabricated conviction) is architecturally right and
several prior-audit sins are fixed (consensus independence, screener/detail
consistency, sector betas, live risk-free). But three structural gaps mean it
still doesn't reason like a disciplined analyst across the whole universe:
(1) it cannot express long-duration compounding, so it mis-levels ~100 of
India's best businesses; (2) two whole archetypes (young/loss-making, fee
financials) have no model at all — 15% of the universe is a permanent no-call
by construction; (3) its confidence never looks at cross-method agreement or
assumption sensitivity, so a BUY can ship while the engine's own methods
disagree 15× (AWL).

---

## Findings (S0 none · S1 three · S2 five · S3/S4 four)

### VAL-01 Plausibility-gate hole: confident BUY/ACC at +60…+100% MoS
- Category: Sanity-bound | Verdict-mapping
- Severity/Likelihood/Effort/Priority: **S1 / High / S** / P0
- Affected scope: 27 published BUY/ACCUMULATE calls (matrix filter `verdict∈{BUY,ACC} & mos>0.60`), 18 at ≥+74%
- Location: `app/engines.py:647` (gate fires only at `mos > 1.0` for non-financials; lenders 0.80, alt models 0.60)
- Evidence: **AWL (Adani Wilmar): BUY, +91.2% MoS, HIGH confidence** — a 2.4%-EBIT-margin edible-oils processor classified CONSUMER, whose Exit-Multiple leg reads ₹1,052 vs price ₹189 (5.6×) before capping; the blend lands just under the +100% gate. **REDINGTON +79% (high conf)** — the very ticker DAT-02's DISTRIBUTION override was built for still ships a confident call. NATCOPHARM +97%, GODFRYPHLP +97%, COALINDIA +84%, INFY +64%, SBIN +68%. The full engine already believes (comment at engines.py:648-656) that a generic model claiming ~+100% on a liquid name is "wrong more often" — +91% is not materially different from +101%.
- Root cause: the implausible-upside threshold is a hard cliff (1.0) instead of a band coherent with the alt-model (0.60) and lender (0.80) gates; nothing between +60% and +100% requires corroboration.
- Why it matters: these are the engine's flagship *bullish* errors — the exact class users act on. Downstream, the FM engine only marks a model SUSPECT at mos ≥ +300%, so all 27 flow into idea ranking as strong "cheap" votes.
- Recommended fix: make the gate a corroboration requirement, not a cliff — a non-financial BUY/ACC with mos > ~0.50 must (a) have cross-method dispersion below a tolerance AND (b) analyst/peer-multiple support (either one missing → LOW CONF, exactly as the lender gate works). Never loosen the 1.0 cliff; tighten toward coherence with 0.60/0.80.
- Verification: re-run `matrix.csv`; the `verdict∈{BUY,ACC} & mos>0.6` set should collapse to names with corroborated upside (COALINDIA/BPCL-type deep-value can legitimately survive with street support; AWL/REDINGTON must not).

### VAL-02 The core model cannot express long-duration compounding (98-name quality cohort)
- Category: Model-selection | Cash-flow/Terminal
- Severity/Likelihood/Effort/Priority: **S1 / High / L** / P0
- Affected scope: 98 names gated `LC_compounder_understated` + the surviving REDUCE band (BAJFINANCE −40%) + DMART-class AVOIDs — roughly 12% of the universe, containing most of its market cap
- Location: `app/derive.py:211` (growth_hi 0.18 cap), `derive.py:335-348` (CAP keyed to sector mature_roic), `derive.py:279-301` (reinvest 0.65/0.75 ceilings), `app/engines.py:665-680` (the −45% gate that turns the resulting error into abstention)
- Evidence (DOSSIERS.md): **DMART** — engine AVOID −47.4% (high conf) vs street +14%; reverse-DCF on the engine's own structure shows the market implies **41.4% stage-1 growth vs the model's hard 18% cap**, with reinvestment forced to the 0.75 ceiling and CAP stuck at 8y because CONSUMER's mature_roic (0.22) makes a store-rollout ROIC look ordinary. **BAJFINANCE** — REDUCE −40.4%: terminal ROE capped at forecast (17.35%), so the RI structurally cannot justify >~2.5× book while the market pays 3.9× for two decades of 20%+ compounding. June's audit table shows the same cohort at −40…−58%; the July fixes moved it +1…+10pp and the −45% gate now hides the residue as LOW CONF.
- Root cause: three interacting conservatisms — growth cap, CAP formula normalized by *sector* mature ROIC (penalizes compounders in high-return sectors), and one-way reinvestment tempering — compress every long-duration franchise into an 8–15y fade that the market prices over 20–30y.
- Why it matters: this is the single biggest driver of the owner's complaint: ~100 marquee names read "no call," and the bearish skew (68% of issued verdicts negative) is largely this bias leaking through on names that miss the gate (REDUCE band).
- Recommended fix (preserves abstention): add an explicit **long-duration compounder archetype** — eligibility earned from evidence (10y ROIC/ROE persistently ≥1.5× Ke, revenue compounding, reinvestment runway), valued with a 3-stage model whose stage-2 length is company-evidenced (not sector-normalized), cross-checked against the name's own 10y multiple band. Names that fail eligibility keep today's gate. Do NOT simply raise growth caps universe-wide.
- Verification: DMART/NESTLEIND/HUL/BAJFINANCE dossier set re-run: intrinsics should move into a −25…+15% band vs market with the verdicts becoming genuine HOLD/REDUCE *calls*, and `LC_compounder_understated` should fall from 98 toward <30 — via modeling, not gate removal.

### VAL-03 Intraday verdict refresh bypasses every plausibility gate
- Category: Verdict-mapping | Reconciliation
- Severity/Likelihood/Effort/Priority: **S1 / Medium / XS** / P0
- Affected scope: whole universe, latent (0 live leaks today — confirmed by matrix scan `gate_leak_high_mos_call = []`)
- Location: `app/ingest/compute_valuations.py:90-108` (`_verdict_from`), called by `refresh_mos()` on every intraday price tick
- Evidence: `_verdict_from` reproduces only the base bands. A batch-time BUY (composite ≥68, reliable=True) whose price falls intraday to mos = +1.4 **stays BUY** — the exact state `engines.py:647` forbids; the lender-0.80 and alt-0.60 gates are equally absent. Conversely a LOW CONF stuck by `current in ("LOW CONF","NO DATA")` can't recover when its gate reason (price-dependent!) clears — verdicts drift from the engine's own law between batches.
- Root cause: the cheap intraday mirror was written before the gate system grew; it now mirrors a subset.
- Recommended fix: extract the full verdict+gate mapping into one shared function used by both `engines.recommend` and `refresh_mos` (the gates need only mos/type/sector/roe/alt-flag — all available or storable on the Valuation row).
- Verification: unit test: batch BUY at +0.6, price −40% intraday → refresh must yield LOW CONF; batch LOW CONF via mos>1.0, price +60% → refresh may re-issue a banded verdict.

### VAL-04 Confidence ignores cross-method agreement and assumption sensitivity
- Category: Confidence
- Severity/Likelihood/Effort/Priority: S2 / High / M / P1
- Affected scope: every confident verdict; measured in the 103-name sample: 30+ names with max/min method dispersion >3×, including **BUYs at 15.1× (AWL), 9.1× (BLS), 6.8× (CHALET), 6.5× (CESC)**; 25+ names whose intrinsic swings >50% across the engine's own ±1% sensitivity grid **with no confidence effect** (BAJAJFINSV 153%, BAJFINANCE 126%)
- Location: `app/engines.py:447` (`conf = data_quality(co)` — data-only), `engines.py:362` (`sensitivity()` computed, returned, never consumed)
- Root cause: confidence = data-quality score only; the brief's (ii) method-agreement and (iii) sensitivity legs were never wired.
- Recommended fix: conviction = f(data tier, dispersion of *computed* methods, sensitivity swing). Wide dispersion or knife-edge sensitivity caps confidence at MEDIUM and, combined with VAL-01, gates extreme-MoS calls.
- Verification: AWL-class names must read MEDIUM-at-best; the `verdict × dispersion` join in `deep.csv` should show no BUY above 3× dispersion.

### VAL-05 Two archetypes have no model at all (15% of the universe is a permanent no-call)
- Category: Model-selection | Data-coverage
- Severity/Likelihood/Effort/Priority: S2 / High / L / P1
- Affected scope: 116 `LC_low_or_negative_roe` (young/loss-making/near-zero ROE) + 32 `LC_fee_financial` + LICI
- Location: `app/engines.py:622-634` and `:613-621` — both paths are pure abstention
- Evidence: the abstentions are *honest* (correct per the prime principle) but permanent: no survival-adjusted young-company model (revenue → target margin → sales-to-capital, survival-weighted) and no fee-annuity model (AUM/volume-driven earnings on P/E vs growth) exist to graduate names out.
- Recommended fix: build both archetype models as *earned* paths — a young name with 3+ years of real statements and visible margin trajectory gets a survival-weighted value at LOW→MEDIUM confidence; a fee financial gets an earnings-power model on its own 10y P/E band. Abstention remains for names that fail input requirements.
- Verification: `LC_low_or_negative_roe + LC_fee_financial` should fall materially with **no** forced confident calls (target: half graduate to banded MEDIUM-conf calls).

### VAL-06 Category error surface: 12 financial-sector names run through the FCFF DCF
- Category: Model-selection
- Severity/Likelihood/Effort/Priority: S2 / Medium / S / P1
- Affected scope: 12 names with `type≠financial` but `valuation_sector∈{BANK,NBFC,INSURANCE}` (ARSSBL, EDELWEISS, RELIGARE, SHAREINDIA, IIFLCAPS, CRAMC, MEDIASSIST, PRUDENT, LLOYDSENT, INDOTHAI, JSWHL, TSFINV)
- Location: `app/engines.py:170` routes by `co["type"]`, classification sets `valuation_sector` independently
- Evidence: **ARSSBL** — a broker valued by FCFF DCF at ₹1,483 vs price ₹538 (+176% → gated LOW CONF); reverse-DCF shows the market implies −30% growth vs the model's +17.8%. The gate saved the output, but the engine ran WACC-DCF mechanics on a balance sheet where debt is raw material — the benchmark's category error, live.
- Recommended fix: reconcile `type` with `valuation_sector` at classification time; fee-financials route to the VAL-05 model, true lenders to RI, holdcos to NAV/SOTP.
- Verification: the class-mismatch list in `matrix.csv` empties.

### VAL-07 Terminal value dominates with no gate (median 69%, >85% for a quarter of the sample)
- Category: Cash-flow/Terminal | Confidence
- S2 / Medium / S / P1 — `deep.csv`: tv_share >0.85 for 24/103 incl. RELIANCE, LT, DMART, NTPC, TITAN, BHARTIARTL. High-TV% means the near-term model does almost nothing; combined with VAL-04, TV share belongs in the confidence function (benchmark C). Fix: TV%>85 caps confidence and surfaces in drivers.

### VAL-08 Alt-model presets are hand-seeded constants; segment engine applies an EV/EBITDA multiple to EBIT
- Category: Reconciliation | Data-coverage
- S2 / Medium / S / P1 — `alt_models.py` SOTP/EV/VNB/combined-ratio inputs are FY26 constants (stamped, DAT-03-gated at +60% — good), but RELIANCE's ₹2.6L-cr-of-value verdict rests on four hand-typed numbers; `segment_sotp.py:29-53` values operating segments at `EBIT × exit_ev_ebitda` — an EBITDA multiple on an EBIT base (systematic understatement mislabeled as market convention). Fix: use EBITDA (or an EBIT multiple), and put preset re-verification on the results-season checklist with a staleness warning after 2 quarters.

### VAL-09 Discount-rate refinements (S3): sector-only beta (no size/illiquidity premium — a micro-cap and Infosys share a beta today), TATASTEEL-class WACC compression (7.97% vs Ke 11.4% via debt weight; floor is g+3% only), DEFENCE terminal growth 6.0% violating the file's own ≤5.5% GDP anchor (`sector_params.py:145`).

### VAL-10 Verdict-ladder asymmetry drives the bearish skew (S3): BUY requires composite ≥68 AND mos >15%, but AVOID needs only mos <−25% (no composite/quality condition) — 143 AVOIDs sit below −60% MoS. With VAL-02's level bias this manufactures the 68% negative skew. Fix with VAL-02, then re-map bands against the post-fix MoS distribution.

### VAL-11 What is WORKING (S4, keep it): consensus independence (stored separately, never blended — verified in `compute_valuations.py:37-50`); screener=detail=one number (TCS 3311.9/+46.0%/BUY both paths); 0 unexplained abstentions; 0 non-positive intrinsics; confidence↔data-tier correlation clean (all thin/partial names are low/medium); live G-sec risk-free with band-guarded refresh; WACC≥g+3% floor; synthetic-price → NO DATA; the DAT-01/02/03 gates all verified live.

---

## Fund-manager propagation (§4)

`manager_engine.triangulate` (app/manager_engine.py:215-258) has its own guardrails
— but they are **looser than the engine's**: SUSPECT only at mos ≤ −75% or ≥ +300%
vs the engine's ±45%/100% gates, and it reads `confidence` (data-only, VAL-04).
Consequences: (a) all 27 VAL-01 gate-hole BUYs enter idea ranking as full-strength
"cheap" votes; (b) VAL-03 drift can feed FM a stale BUY the valuation engine
would forbid; (c) VAL-04 means FM cannot distinguish a corroborated +30% from a
15×-dispersion +90%.

**Recommended interface contract** (valuation → FM): every value ships as
`{intrinsic, mos, verdict, confidence, data_tier, archetype, method,
method_dispersion, sensitivity_swing, tv_share, gate_state}` — and FM treats
`gate_state ≠ CLEAN` or dispersion above tolerance as SUSPECT regardless of mos.
FM's own bounds should tighten to at most the engine's (±45%/+100%).

---

## Prioritised remediation backlog

| P | Item | Findings | Effort |
|---|---|---|---|
| P0 | Shared verdict+gate function for batch & intraday | VAL-03 | XS |
| P0 | Close the +60…+100% gate hole with corroboration requirements | VAL-01 | S |
| P0 | Long-duration compounder archetype (evidence-earned 3-stage) | VAL-02 | L |
| P1 | Conviction = data × method-agreement × sensitivity (+ TV-share cap) | VAL-04, VAL-07 | M |
| P1 | Young-company (survival-adjusted) + fee-annuity models | VAL-05 | L |
| P1 | type↔sector reconciliation; broker/holdco routing | VAL-06 | S |
| P1 | Segment engine EBITDA basis + preset staleness discipline | VAL-08 | S |
| P2 | Size/illiquidity premium; WACC floor vs Ke; DEFENCE g fix | VAL-09 | S |
| P2 | Re-map verdict bands post-VAL-02 (fix the AVOID asymmetry) | VAL-10 | S |
| P3 | Convert honest data-abstentions via ingestion backfill (34 stubs) | — | M |

Sequencing note: P0s first because they stop wrong *confident* output; the
archetype work (VAL-02/05) is what converts abstention volume into real calls —
that is the only legitimate path to fewer no-calls. **At no point should any
gate be loosened without its replacement corroboration being live.**

## Prior-audit reconciliation (VALUATION_AUDIT_2026-06-11.md and related)

| Prior issue | Status |
|---|---|
| Fade-from-year-1 DCF understating compounders | FIXED (two-stage hold+fade, parity 60/60) — residual level bias remains (VAL-02) |
| Flat 8y horizon | FIXED (CAP 8–15y) but sector-normalized ROIC misses compounders (VAL-02) |
| Flat beta ~1.0 for everyone | FIXED at sector level (0.58–1.35); per-company/size premium still absent (VAL-09) |
| ~60% analyst-consensus blend in the intrinsic | FIXED — verified none; stored separately (VAL-11) |
| Detail vs screener showing two intrinsics | FIXED — one number, verified (VAL-11) |
| VEDL SOTP stale share count (DAT-01) | FIXED — live share count, verified in code and matrix |
| REDINGTON/SENCO/CHENNPETRO confident BUYs (DAT-02) | PARTIAL — gate tightened to 1.0, but REDINGTON *still* prints +79% BUY through the hole (VAL-01) |
| ITC preset divergence (DAT-03) | FIXED — +60% alt gate live (1 name currently caught) |
| M&M peak-cycle +114% | FIXED (12% semi-cyclical cap) — M&M now REDUCE band |
| 38% AVOID concentration (#81) | PARTIAL — now 32% AVOID; root cause persists as VAL-02/VAL-10 |
