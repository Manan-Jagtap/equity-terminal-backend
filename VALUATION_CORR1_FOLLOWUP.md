# CORR-1 follow-up — where the remaining over-valuation actually lives

**Date:** 2026-07-24 · **Method:** hermetic replay of the committed calibration
fixture (`tests/_calib_replay.py`, 515 names) scored against the 314 in-fixture
rows of `VALUATION_GROUNDTRUTH_2026-07/calibration_targets.csv`. Read-only —
no engine change was shipped from this analysis.

## 1. Baseline

`tests/calibration_check.py --baseline tests/calib_baseline.json`

```
WITHIN-BAND (correction targets): 48/314 = 15.3%
AGREE in-band (do-not-break set):  98/198
✅ 0 hard breaks
```

## 2. The bias is one-sided and universal

| | count |
|---|---|
| ABOVE target band | **246** |
| in band | 48 |
| BELOW target band | 20 |

Median engine/target-mid by archetype ranges **x1.26 (ENERGY) → x2.98 (LOGISTICS)**;
every non-financial archetype is rich. Only BANK (x0.65) and the small
NBFC/TELECOM cohorts sit low. This is a **level** problem, not dispersion.

Worst cohorts by absolute misses: MANUFACTURING 3/42, IT_SERVICES 1/30,
CONSUMER 7/32, CONSUMER_DISC 4/27, CAPITAL_GOODS 6/28, PHARMA 2/26,
CHEMICALS 3/25.

## 3. CORR-1 is ALREADY SHIPPED — and is not the remaining cause

The ground-truth spec's CORR-1 prescribed deflating the relative legs. Verified
in the current tree, all three parts already landed (as FIX-11):

| CORR-1 part | Spec | Current code | Status |
|---|---|---|---|
| (a) cross-check clamp | `[0.5, 2.2]` → `[0.6, 1.6]` | `LO, HI = 0.6 * primary, 1.6 * primary` (engines.py) | ✅ shipped |
| (b) blend weights | Exit 0.30→0.20, DCF 0.55→0.65 | `nonfin: DCF 0.65 / Exit 0.20 / P-E 0.15` | ✅ shipped |
| (c) exit multiples | CONSUMER 42→34, CONSUMER_DISC 38→30, CAPITAL_GOODS 32→27, DEFENCE 34→28 | 34 / 30 / 27 / 28 in sector_params.py | ✅ shipped |

## 4. Decomposition — the DCF primary is the driver

Because every cross-check is clamped to `[0.6, 1.6] × primary`, the legs cannot
explain a ~1.8× overshoot. Measured directly over the same 313 names:

```
median PRIMARY (DCF/RI) / target_mid = x1.59
median BLENDED           / target_mid = x1.79
```

**~75% of the overshoot is the primary DCF itself**; the clamped relative legs
add the remaining ~13% on top (1.79 / 1.59 = 1.13). Further deflating the legs
therefore cannot close the gap — the primary must come down.

## 5. Terminal growth is NOT a sufficient lever

Global terminal-growth haircut, everything else held constant:

| Δ terminal growth | median eng/target | within-band |
|---|---|---|
| 0 (today) | x1.82 | 48/314 = 15.3% |
| −50 bp | x1.76 | 55/314 = 17.5% |
| −100 bp | x1.68 | 57/314 = 18.2% |
| −150 bp | x1.62 | 64/314 = 20.4% |
| −200 bp | x1.57 | 71/314 = 22.6% |

Even an aggressive −200 bp (≈5.5% → 3.5% perpetual) leaves the engine **57%
rich** and reaches only 22.6%. The terminal assumption is a contributor, not the
cause.

## 6. Where to look next (not yet executed)

The residual sits in the **explicit forecast stage**, not the terminal knob.
Ranked hypotheses for the next batch, each independently testable on this
harness:

1. **Forecast-stage growth too high / faded too slowly** — the projected
   revenue/EBIT path compounds before the terminal value is even reached.
   Test: replace the forecast growth vector with a faster fade to `mature_roic`
   economics and re-measure.
2. **Reinvestment too low in the FCFF bridge** — if growth is credited without
   charging the capex/working-capital needed to fund it, FCFF is overstated at
   every horizon. Test: enforce `reinvestment = g / ROIC` consistency and
   re-measure (this is the textbook link flagged in
   [institutional-valuation-methodology]).
3. **Discount rate too low** — ERP/beta calibration; smaller lever than (1)/(2)
   but compounds with them.

**Do not** pre-tune verdict thresholds (CORR-5) before this level fix lands —
the spec is explicit on that ordering, and the current thresholds are calibrated
against the *inflated* distribution.

## 7. Regression bar for whichever fix lands

Unchanged and non-negotiable: `calibration_targets.csv` within-band must not
fall, the 198-row Agree do-not-break set must stay unbroken (0 hard breaks),
and both parity harnesses must be regenerated green (engine.js 60/60,
derive.js 48/48) since `engines.py`/`sector_params.py` are parity-locked.

**Harness note:** the replay needs Python ≥3.10 (the app uses PEP-604 unions).
A local 3.9 venv reports 515 replay errors and a false 0.0% — use a 3.13
interpreter (CI runs 3.13).
