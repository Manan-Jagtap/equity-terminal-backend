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

---

## CORR-3c — MANUFACTURING (catch-all archetype), 2026-07-27

**Starting point:** 9/42 within band, the worst archetype in the set.

**What MANUFACTURING actually is.** It is `DEFAULT_SECTOR`, so every name the
keyword classifier cannot place lands there and inherits factory economics.
Raw vendor sectors across the 42: Misc. Fabricated Products (8), Services (6),
Constr.-Supplies & Fixtures (6), Electronic Instr. & Controls (5), Fabricated
Plastic & Rubber (4), Rental & Leasing (3), Business Services (2), Unknown (2),
Recreational Activities (2), and one each of Containers & Packaging,
Diversified, Furniture & Fixtures, Semiconductors. Roughly 38% are not
manufacturers.

**Shipped:** five per-ticker pins for names whose real archetype already exists
and is unambiguous — ADANIPORTS, CONCOR, BLUEDART, JSWINFRA, TVSSCS →
LOGISTICS. Same failure mode as REDINGTON: the vendor sends the bare string
`"Services"`, which no keyword can place. TVSSCS improves x2.11 → x1.66;
the rest are flat-to-marginal. **Overall within-band is unchanged at 33.4%** —
this is a correctness fix (sector labels, peer sets, sector screens), not a
calibration win, and is reported as such.

**Deliberately NOT shipped:** MHRIL and WONDERLA (vendor "Recreational
Activities"). They are plainly not manufacturers, but CONSUMER_DISC is the
*richer* archetype (mature_roic 0.18 / exit P/E 30 vs 0.15 / 28) and both
already value ABOVE band, so relabelling made them worse: MHRIL x2.72 → x3.19,
WONDERLA x1.85 → x2.06. Pinned by a test so a later "completion" of the
reclassification has to be deliberate.

### The real disease is not classification

Reclassification moved within-band by exactly zero, so the cohort was measured
directly:

- **30 ABOVE / 9 IN BAND / 3 BELOW**, median intrinsic / band-midpoint **x1.77**.
- **All 42 price off `primary_method = "FCFF DCF"`.** The exit multiple never
  binds for a single one of them.

That last point was confirmed by sweep, not assumed: exit_pe 28 → 24 → 22 → 20
→ 18 (with exit_ev_ebitda tracking down) left within-band at **33.4% at every
step**. MANUFACTURING's "rich 28x P/E" — the thing the comment at
`sector_params.py:147` blames for commodity-cyclicals falling through — is
inert for this cohort. Any future fix aimed at the exit multiple here is
aimed at nothing.

Terminal growth *is* live: tg 0.050 → 0.040 → 0.035 moves MFG 9 → 10 → 11/42
and overall 33.4% → 33.8% → 34.1%. Beta adds nothing independent of it
(beta 1.20/tg 0.035 == beta 1.00/tg 0.035).

**That lever was deliberately left alone.** A 3.5% *perpetual nominal* growth
rate for Indian industrials, against a ~6.5% risk-free and ~10% nominal GDP,
implies permanent real decline. It would buy +0.7pp on this fixture by
asserting something untrue about the economy — curve-fitting to the
calibration set, not a valuation fix.

**Next lever for this cohort (unstarted):** a 74% median level bias across an
entire cohort, with the terminal multiple inert, points at stage-1 forecast
growth being extrapolated too aggressively off recent prints. That is the
CORR-1 disease and is cross-sector, not MANUFACTURING-specific — it should be
fixed once in the forecast, not per-archetype.

---

## CORR-1 — stage-1 growth must be earned from quality, 2026-07-27

The cross-sector level bias the CORR-3c pass pointed at. Diagnosed on the
314-name calibration set by grouping every name by its derived `rev_growth`:

| stage-1 growth | n | median intrinsic / band-mid | within band |
|---|---|---|---|
| **at the 18% cap** | **104** | **x1.73** | **23%** |
| 12–17.5% | 78 | x1.32 | 44% |
| 8–12% | 94 | x1.26 | 36% |
| < 8% | 38 | x1.23 | 37% |

**A third of the universe sat pinned at exactly the 18% clamp, and that cohort
was the disease.** Everything below the cap was calibrated roughly twice as
well.

Grouping by horizon confirmed it from the other side:

| `fade_years` | n | median ratio | within band |
|---|---|---|---|
| 8 | 140 | x1.60 | 21% |
| 10 | 69 | x1.39 | 32% |
| 12 | 38 | x1.28 | 45% |
| 15 | 67 | x1.23 | **55%** |

Counter-intuitive until you recall `fade_years` is assigned from ROIC
durability: the SHORT-horizon names are the ordinary ones, and they are the
over-valued ones. The bias is **high growth granted to low-quality names**.

### The inconsistency

`fade_years` earns the growth RUNWAY from `roic_q`, on its own stated
principle — *"the runway is earned from QUALITY (ROIC durability), NOT current
growth"*. But the growth RATE ceiling was a flat 18% for every non-cyclical.
The horizon asked for evidence; the rate did not.

### Fix

`_growth_ceiling(roic_used, mature_roic)` in `derive.py`, mirrored in
`derive.js`, applied immediately after the existing symmetric 8% high-ROIC
*floor*:

- `roic_q >= 1.1` → **0.18, unchanged.** Every name that out-earns its sector
  keeps the full rate.
- below → **0.10**, ≈ India's long-run *nominal* GDP growth. A business with no
  demonstrated return advantage is not assumed to outgrow the economy it
  operates in.

1.1 is deliberately the same threshold at which `fade_years` grants its first
horizon step-up, so the rate and the runway key off one piece of evidence.
One-directional: it only ever LOWERS an unearned rate.

**Result: 33.0% → 38.5% within band (105 → 120 names), 0 hard breaks.**
One soft abstention (TRAVELFOOD moves REDUCE → LOW CONF — the engine declining
to call, not calling wrongly).

### What was rejected, and why

- **Tiering the higher bands too** (0.18/0.15/0.13, base 0.10) scored **40.4%**
  but broke LUPIN: an ACCUMULATE at +8.7% MoS became a HOLD at −8.0% against a
  ground-truth BUY-zone. Every graduated variant tried broke that same name.
  Not taken — the standing bar is 0 hard breaks.
- **base 0.09** scored +0.3pp better than 0.10 with 0 breaks, and was still not
  taken: it has no economic anchor. 0.10 is nominal GDP; 0.09 is a fitted
  constant. A test pins the 0.10 and records this.
