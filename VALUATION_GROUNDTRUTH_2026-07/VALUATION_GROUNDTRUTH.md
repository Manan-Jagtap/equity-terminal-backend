# Whole-Universe Valuation Cross-Check — Ground Truth (2026-07-20)

An independent, buy-side second opinion on every company the engine covers,
compared verdict-by-verdict against the engine's live output. **Read-only; no
engine code changed.** Artifacts: `groundtruth.csv` (all 1,001 rows),
`calibration_targets.csv` (319 Tier-A corrections). Engine state compared:
prod as of 20 Jul 2026 (pre-deployment of the VAL-01/03 gate fixes in commit
18a5125 — noted where those already resolve a row).

## Independent method (deliberately orthogonal to the engine)
Two-leg justified-multiple earnings-power valuation on the sustainable-growth
identity — NOT a re-run of the engine's DCF blend:
- Ke = 6.2% (live 10Y G-sec) + sector beta × 5% ERP (betas audited sane).
- Earnings leg: two-stage EPV — stage-1 growth from the company's OWN
  statement history where held (104 detail payloads; deterministic derive
  output, not a model number), else ROE × retention; quality-earned stage
  length (10y elite secular / 8 / 5 / 3 cyclical); Gordon terminal at
  min(5%, Ke−2%) with fade-consistent payout.
- Book leg: justified P/B = (ROE_sust − g)/(Ke − g), lender peak-haircut.
- Cyclicals: EPS normalized toward mid-cycle before capitalizing (peak
  haircut / partial trough uplift vs sector-mature ROE).
- Blend 0.80/0.20 secular, 0.65/0.35 other non-fin, 0.40/0.60 financials;
  range width from leg disagreement; **I abstain where my own method cannot
  support a claim** (near-zero ROE, legs beyond tolerance, spot-multiples-only
  on long-duration growth) — tagged `method_limited`, never counted as an
  engine error. Vendor zero-sentinel ROEs treated as missing (a real trap —
  it silently voided TCS/HDFCBANK until caught).
- Manual analyst layer: the 8 audit dossiers + gate-simulation set
  (TCS, DMART, BAJFINANCE, TATASTEEL, AWL, RELIANCE, SBILIFE, ARSSBL,
  REDINGTON + the 16-name +50%-MoS cohort) override/annotate the systematic
  numbers where statements-grounded judgment differs.

## Coverage (honest accounting)
| Tier | Scope | Covered | Depth |
|---|---|--:|---|
| A | all confident BUY/ACC/REDUCE/AVOID | **549/549** | systematic 2-leg + manual layer on all 28 BUYs, extremes, marquee (~45 names) |
| B | confident HOLD | 122/122 | lighter systematic check |
| C | abstained tail | 330/330 | abstention-justification classified (not valued) |
| — | method-limited (my method can't check) | 10 | declared unchecked, excluded from engine-error counts |
| — | known live data defects (platform audit) | 6 | recorded as cause=data, not valued |

## Headline results

**1. The AVOID wall is largely RIGHT.** Of 319 engine AVOIDs, my independent
read is also bearish on **273 (86%)**; only 8 names (2.5%) do I read HOLD-or-
better. The valuation audit's "bearish skew" concern is real but its damage is
concentrated in the *gated* quality cohort (LOW CONF), not in the issued
AVOIDs — an important refinement of VAL-02/VAL-10.

**2. The BUY/ACCUMULATE list is the least reliable slice of the product.**
Of 95 positive calls, I corroborate only **22 (23%)**; I read **46 (48%)
outright bearish** (e.g. WIPRO eng +52% vs me −37%; REDINGTON eng +79% vs me
−40%; AWL eng +91% vs me −15%), 12 HOLD, 15 I abstain on. Mechanism: adverse
selection — only extreme-model-upside names clear `composite ≥ 68 AND
mos > 0.15`, and extreme model upside is exactly where the inflated
exit-multiple leg dominates. The already-shipped VAL-01 corroboration gate
(commit 18a5125) kills the worst of these (AWL/BLS/SAMHI/REDINGTON verified
by simulation); the calibration targets cover the rest.

**3. Tier-A class distribution (549):** Agree 197 + Adjacent 25 (40%) ·
Direction-agree-magnitude-off 168 (31%) · Disagree 52 (9%) ·
Engine-should-have-abstained 99 (18%, mostly names where independent legs
disagree beyond tolerance — corroborating the VAL-04 confidence work) ·
Method-limited 8.

**4. Cause attribution across all non-agreeing confident calls:**
| Cause (direction: engine vs me) | n | Correction |
|---|--:|---|
| engine-RICH: exit-multiple / sector-P/E leg inflation | 124 | CORR-1 |
| engine-RICH: thin-margin name on rich sector multiples (AWL-class) | 56 | CORR-2 |
| threshold/judgment inside ±0.30 (not a defect) | 135 | CORR-5 recalibration |
| engine-BEARISH: VAL-02 family (fin terminal-ROE cap, compounder caps, trough+asymmetry) | 12 | CORR-4 (= P0-3) |
| lender RI above justified-P/B | 2 | CORR-1/4 review |

**5. Abstention audit (Tier C, 330):** 270 justified (82%) · 55 wrongly
abstained — my model reaches a supported value on: PSU/large lenders gated by
the blanket +80% lender-divergence rule though justified-P/B supports the value
(LICHSGFIN, PFC, RECLTD, BANKBARODA, MAHABANK…), ITC (my +6% ACC-zone vs the
stale-preset +113% that trips the alt gate — VAL-08 confirmed), and the
compounder-gate cohort · 5 data defects (VAL/platform-audit bugs).

## Ranked engine-correction spec (Opus-executable, by names fixed)

**CORR-1 — deflate the relative cross-check legs (≈124 live names + tail).**
Module: `app/engines.py` `_BLEND_WEIGHTS`, `blended()` clamp, and
`app/sector_params.py` exit multiples. Change: (a) tighten the cross-check
clamp from `[0.5×, 2.2×]` of primary to `[0.6×, 1.6×]`; (b) cut Exit-Multiple
weight 0.30 → 0.20 (non-fin) with DCF 0.55 → 0.65; (c) re-base the four most
inflated exit multiples toward sector trailing medians (CONSUMER exit_pe 42→34,
CONSUMER_DISC 38→30, CAPITAL_GOODS 32→27, DEFENCE 34→28). PARITY: mirror in
`src/lib/engine.js` + regenerate `tests/gen_parity_cases.py` fixtures + both
harnesses green. Regression: re-run `calibration_targets.csv` — the 124
engine-rich rows must move inside their `target_lo–target_hi` bands; the 22
corroborated BUYs must survive.

**CORR-2 — margin-based economics override (≈56 names).** Module:
`app/sector_params.py` classification (+ `derive.py` hook). Change: a company
with through-cycle net margin < 4% and revenue > ₹5,000 cr classified into
CONSUMER/CONSUMER_DISC/MANUFACTURING re-routes to DISTRIBUTION-economics
params (14×/8× exits, 12% growth tier). AWL is the type case. Regression:
`groundtruth.csv` rows tagged AWL-class land within bands; no cyclical
mislabeled (guard: exclude METAL/ENERGY already-cyclical).

**CORR-3 — convert the wrongly-abstained 55 (needs no gate loosening).**
(a) Lender-divergence gate becomes corroboration-aware: fin + mos ≥ 0.80 keeps
the confident call IF justified-P/B (already computed in `gordon_pb_value`)
independently exceeds price by ≥ 25%; else LOW CONF as today — converts
PFC/RECLTD-class PSU lenders. Module: `engines.py` lender gate.
(b) Re-seed the ITC/RELIANCE SOTP presets from FY26 filings (VAL-08 discipline)
— ITC's +113% is a stale-constant artifact; my independent +6% is the target.
(c) The fee-financial and young-company archetype models (VAL-05, specced in
the valuation audit) convert the remainder.

**CORR-4 — the compounder archetype (P0-3, already specced).** This dataset
adds its regression suite: per-name target bands for DMART/HDFCBANK/
BAJFINANCE/NESTLEIND/HINDUNILVR-class rows in `calibration_targets.csv`
(e.g. HDFCBANK: engine REDUCE −10% → target BUY-zone band ₹660–1,271 mid
+18%; DMART: AVOID −47% → abstain-or-HOLD pending the archetype model).

**CORR-5 — post-fix threshold recalibration (135 names).** After CORR-1/2
land, re-run the verdict distribution; widen the HOLD band to ±12% and require
corroboration count ≥ 2 for BUY (aligns the ladder with the corrected MoS
distribution). Do NOT pre-tune thresholds before the level fixes.

## Prior-audit reconciliation
- **VAL-01 VALIDATED and sharpened**: 180 engine-rich confident calls (124+56)
  — the gate hole was the tip; the exit-multiple leg is the iceberg. Shipped
  gate (18a5125) verified catching AWL/REDINGTON/BLS/SAMHI.
- **VAL-02 VALIDATED with a twist**: the bearish bias is real (DMART/
  BAJFINANCE/HDFCBANK targets included) but is 86%-quarantined by the gates;
  its live-verdict footprint is ~12 names, not the AVOID wall.
- **VAL-05 VALIDATED**: 55 wrongly-abstained; PSU-lender cohort adds a
  new, cheaper conversion path (CORR-3a) the audit hadn't isolated.
- **VAL-08 VALIDATED with numbers**: ITC preset +113% vs independent +6%.
- **VAL-10 PARTIALLY REFUTED**: cyclical AVOIDs are mostly corroborated
  (TATASTEEL: I'm *more* bearish than the engine); the asymmetry problem is
  about extreme magnitudes, not direction.
- **Platform-audit data defects**: 6 names recorded, not valued (DATA-01/02).

## Calibration targets
`calibration_targets.csv`: 319 Tier-A rows — ticker, archetype, engine
verdict/MoS, target verdict, target lo–hi band, diagnosed cause. Use as the
regression suite for CORR-1..5: after each correction, re-run the engine over
these names; success = target-verdict match ≥ 80% within-band, with ZERO
regressions among the 197 Agree rows (they are the do-not-break set).
