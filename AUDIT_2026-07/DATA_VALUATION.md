# Appendix — Data Integrity & Valuation Correctness (Agent 3)

**Headline:** The parity-tested core engine (FCFF DCF + Residual Income) is genuinely sound — reproduced to the last decimal by independent hand re-derivation; the June single-source/independence/consensus-overlay defects are properly fixed. **But the models OUTSIDE the parity contract (alt_models.py SOTP + insurer appraisal) print materially wrong published fair values and ≥2 flipped verdicts, and the implausible-upside gate is loose enough to pass confident +120–197% BUYs on sector-misfit names.** Counts — S0:1 · S1:1 · S2:2 · S3:1.

---

### [DAT-01] SOTP divides segment EV by a hardcoded stale share count — ~2× over-valuation, flipped verdicts (LIVE NOW)
- **Domain:** Data & Valuation  **Severity:** S0 (VEDL/BAJAJFINSV wrong published verdict) / S1 overall  **Likelihood:** High  **Effort:** XS  **Priority:** P0  **Status:** New
- **Location:** app/alt_models.py:138-155 sotp_value() — divides by `p["shares"]` (constant in SOTP_PRESETS) instead of live `co["shares"]`
- **Evidence (live 2026-07-19):** published intrinsic = (Σ segment EV − net_debt) / preset_shares exactly. Preset shares are stale vs the live DB share count used by every price ratio:

  | Ticker | preset sh | live sh | published IV | IV at live sh | overstated | published | correct |
  |---|--:|--:|--:|--:|--:|---|---|
  | **VEDL** | 391.0 | 1027.57 | **₹575.45 (BUY +123%)** | ₹218.96 | +163% | **BUY** | **REDUCE/AVOID (−15%)** |
  | **BAJAJFINSV** | 159.5 | 322.61 | ₹1868 (HOLD) | ₹924 | +102% | HOLD | AVOID (−50%) |
  | **GRASIM** | 68.0 | 140.85 | ₹3309 (HOLD) | ₹1597 | +107% | HOLD | AVOID |
  | ONGC | 1258.0 | 1512.09 | ₹261.5 (HOLD) | ₹217.6 | +20% | HOLD | REDUCE (−12%) |
  | LT | 137.5 | 163.92 | ₹4000 (HOLD) | ₹3355 | +19% | HOLD | HOLD (borderline) |

  BAJAJFINSV preset (159.5) predates its 2024 1:1 bonus (live 322.61 correct). RELIANCE/ITC/BAJAJHLDNG presets happen to match live shares → unaffected. VEDL confirmed live: method=Sum-of-the-Parts, intrinsic=575.45=225000/391, verdict=BUY.
- **Why it matters:** A user sees VEDL = BUY, ₹575, +123% upside; correct SOTP says ₹219, a downside. MoS is meaningless (intrinsic on a different per-share basis than price). Systematic across the conglomerate cohort, always biased upward.
- **Fix:** sotp_value() divides equity by live `co["shares"]`; delete `shares` from SOTP_PRESETS entirely. Add a guard test: abs(preset/live − 1) < 0.05.
- **Verification:** Recompute the five; VEDL → ~₹219, out of BUY; MoS uses the same share basis as price.

### [DAT-02] Implausible-MoS gate at +200% is too loose — confident +120–197% BUYs on sector-misfit names; "high confidence" reflects data completeness, not model fit
- **Domain:** Data & Valuation  **Severity:** S1  **Likelihood:** High  **Effort:** M  **Priority:** P1  **Status:** Partially fixed / Regressed
- **Location:** app/engines.py:632 (`elif mos>2.0: LOW CONF`); app/data_quality.py (checks presence, never applicability); app/sector_params.py (distributor/refiner classification)
- **Evidence (live, all confidence:high, full 5-yr statements — not thin data):**
  - **REDINGTON** (IT-products distributor, 2% EBIT): classified MANUFACTURING → gets the 18% growth cap + 28× exit P/E + 15× EV/EBITDA meant for quality mfrs → blended ₹749.7 vs ₹275 = **BUY +173%**.
  - **SENCO** (small-cap jeweller): **BUY +197%** while flagged "Price history is synthetic" yet confidence:high (0.9). Sits just under the 2.0 gate.
  - **CHENNPETRO** (cyclical refiner): **BUY +119%**, DCF capitalises a peak-cycle 27.9% ROE.
  - 125 names high-conf BUY/ACCUMULATE; several dozen >+100% MoS.
- **Why it matters:** The most consequential outputs ("BUY +173%, high confidence"). data_quality can't see the sector model doesn't fit the business. The live 31-day track record (DAT-05) shows this cohort underperforming.
- **Fix:** (a) Curve the gate (LOW-CONF at MoS>1.0–1.25 for non-conglomerate single-engine names, or scale the haircut with MoS); (b) route distribution/trading businesses to a low-multiple sector (not MANUFACTURING's 28× P/E); (c) feed a model-fit signal (margin vs sector, MoS magnitude) into data_quality so confidence reflects fit.
- **Verification:** Re-run universe; REDINGTON/SENCO/CHENNPETRO → LOW CONF or defensible low MoS.

### [DAT-03] Alt-model (SOTP + insurer) inputs are hardcoded, illustrative, and go stale
- **Domain:** Data & Valuation  **Severity:** S2  **Likelihood:** Med  **Effort:** M  **Priority:** P2  **Status:** New
- **Location:** app/alt_models.py SOTP_PRESETS (segment EVs ₹cr) + INSURER_EV (EV / VNB per share)
- **Evidence:** RELIANCE(₹1548), ONGC(₹262), ITC, LT, GRASIM, VEDL, SBILIFE(₹1697.9), HDFCLIFE(₹565), ICICIPRULI derive their published headline intrinsic from static constants (Reliance "Jio ₹11,00,000cr", SBILIFE "EV ₹805.40/sh, VNB ₹59.5"). SBILIFE's VNB multiple is pinned at its ceiling 15.0 (_vnb_multiple clamp). These update only on code edit; EV drifts every quarter.
- **Mitigations (credit):** capped at MEDIUM confidence, labelled "ILLUSTRATIVE — verify", clamped to a P/EV band.
- **Fix:** Move constants to a dated, version-stamped data file; surface as-of date in UI; staleness warning if EV as-of > 1 reporting cycle; unit test pinning each preset's implied per-share output.

### [DAT-04] "Per-company calculated beta" is NOT in effect — every name uses the flat sector-prior beta; the regression module is dead in prod
- **Domain:** Data & Valuation (truth-in-model)  **Severity:** S3  **Likelihood:** High  **Effort:** S  **Priority:** P2  **Status:** Partially fixed (no longer 1.0, but not per-company)
- **Location:** app/beta.py (regression), app/assemble.py:150-162 (reads cache), app/derive.py (sector fallback)
- **Evidence:** Every sampled name equals its sector prior exactly — INFY/WIPRO/HCLTECH/TCS all 0.85; ICICI/AXIS/KOTAK/HDFCBANK/SBIN all 0.95; MARUTI 1.10; TATASTEEL 1.30 — and the `_drivers.beta` provenance string ("calc β = shrink(...)") is empty for every name → beta_for() returns None universally → computed_betas_v1 KV cache is empty → the regression path never fires live.
- **Why it matters:** Ke doesn't vary within a sector; contradicts the stated methodology ("real CALCULATED equity beta per name") — truth-in-marketing (Compliance).
- **Fix:** Ensure beta.compute_all(db) runs in the scheduler and writes the cache (verify the Dhan NIFTY series is reachable and not exception-swallowed), OR update docs/landing copy to say betas are sector-level.
- **Verification:** After a batch run, _drivers.beta reads "calc β …" and betas deviate from round sector priors.

### [DAT-05] Public track record shows NEGATIVE BUY−AVOID spread + 35% BUY win-rate — honestly built, but confident BUYs empirically unvalidated
- **Domain:** Data & Valuation  **Severity:** S2  **Likelihood:** High  **Effort:** S (disclosure)  **Priority:** P2  **Status:** New
- **Location:** GET /api/backtest, app/backtest.py
- **Evidence (live 2026-07-18):** tracking_since 2026-06-11, 31 snapshot days, 1,370 calls. **BUY−AVOID spread = −1.03%**. AVOID cohort +1.85% (best) vs BUY +0.83%; BUY win-rate 34.9%, AVOID 52.7%. Names called BUY under-performed names called AVOID.
- **Methodology sound (credit):** append-only idempotent ledger, no backfill, corp-action-adjusted total returns, benchmark labelled "equal-weight tracked universe, NOT NIFTY-TRI", NO-DATA/LOW-CONF excluded from scoring. Minor: no transaction-cost/slippage; universe benchmark drops names lacking a current price (mild survivorship tilt).
- **Why it matters:** Window is short (31 days, statistically insignificant) so not a defect itself — but the product markets predictive verdicts and the only hard evidence shows no edge yet, concentrated in the over-valued-BUY cohort of DAT-02. Never frame as proven skill (Compliance).
- **Fix:** Prominent sample-size/significance caveat; add transaction-cost assumptions; keep running.

---

## What PASSES (independent verification — do not re-flag)
- **Parity: 60/60 + 48/48**, on committed AND freshly-regenerated cases (no drift in valuation math).
- **Engine maths CORRECT** — re-implemented FCFF DCF + RI from scratch, reproduced API to the decimal: TCS ke 0.10970, wacc 0.107769, intrinsic ₹2772.76; SBIN ke 0.11470, bvps0 652.39, intrinsic ₹1449.36. Two-stage fade, terminal reinvestment g/ROIC, WACC>g+3% floor, net-debt bridge, ₹cr÷cr-shares units all check out. No divide-by-zero/double-count in the core.
- **Single source of truth PASS** — detail == list == recompute to the decimal (TCS/HDFCBANK/RELIANCE/ONGC/SBILIFE/SBIN/BAJFINANCE).
- **Corporate actions NOT double-applied** — corporate_actions.py idempotent; RELIANCE 1:1 bonus single adjustment, no discontinuity at Oct-2024 ex-date.
- **Freshness GOOD** — live price == latest close (2026-07-16); risk-free from live 10Y G-sec (6.72%).
- **Guardrails fire** — lender divergence (LICHSGFIN +141%→LOW CONF, terminal_roe capped), fee-financial (CAMS/BSE/CDSL→LOW CONF), insurer (EV+VNB), missing-data (no null-sector/missing-equity name gets a confident verdict). But data_quality judges presence only → DAT-02.
- **7-factor Alpha Score** matches marketing; momentum 12-1 with 21-day skip (no look-ahead). Note: value factor ingests mos → inherits DCF-misfit bias from DAT-02.
- **No prompt-injection/directive text** found in any file/DB/API payload. alt_models.py header "SHOULD BE VERIFIED against latest disclosures" is a legitimate maintainer comment, treated as data.

## Prior-audit reconciliation (numeric)
| Prior | Status | Evidence |
|---|---|---|
| C1 DCF on yfinance/flat-1.0 beta | Partially fixed | assumptions from stored 5-yr statements; beta varies by sector but per-company regression dead (DAT-04) |
| C2 page ≠ screener intrinsic | Fixed | detail == list to decimal, 8 tickers |
| C3 TRIM verdict | Fixed (backend) | no TRIM in engines.py (but FE still emits → ARC-01) |
| C4 consensus overlay defeats independence | Fixed | no analyst blend; consensus a separate labelled block |
| C5 dead compute_valuations | Fixed | models.Valuation exists + read |
| C6 HDFCBANK mis-templated NBFC, ROE 68-92% | Fixed | HDFCBANK vsec=BANK, roe=12.97% (net-worth based) |
| C7 coarse taxonomy, dead sector P/E | Fixed | 25+ valuation sectors; classifier tested |
| Insurers/conglomerates LOW CONF fence | Changed | now alt_models MEDIUM conf — upgrade, but DAT-01/03 |

## Quick wins
1. **DAT-01 (XS):** sotp_value() divides by live co["shares"], delete shares from SOTP_PRESETS. Fixes VEDL/BAJAJFINSV/GRASIM/ONGC/LT immediately.
2. **DAT-04 (S):** make the beta batch populate computed_betas_v1, or correct the "calculated per-name beta" copy.
3. **DAT-03 (S):** as-of date + unit test on each alt-model preset.
4. **DAT-05:** sample-size caveat on the public track record.

## Cross-lane observations
- **Compliance/truth-in-marketing:** (a) landing claims per-name calculated beta that isn't live (DAT-04); (b) track record shows negative BUY−AVOID edge over 31d — never frame as proven skill (DAT-05); (c) SOTP/insurer numbers "ILLUSTRATIVE" but rendered as precise fair values + actionable verdicts (DAT-01/03). Independence claim is genuinely true now — good.
- **Cleanup:** app/ still carries .bak files + _audit_test.db (June §3 incomplete).
