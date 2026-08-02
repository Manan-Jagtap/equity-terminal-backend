# The "level bias" is mostly DISPERSION — diagnosis, 2026-08-02

Run against the 73-row fresh ground-truth tranche (72 valued).

## The headline correction

The engine's under-valuation was framed as a **level bias** — a systematic
downward offset to be closed by finding the mis-set parameter. **That framing is
wrong**, and every lever tested behaves exactly as the wrong framing predicts:
the median moves, the hit rate does not.

Distribution of engine IV ÷ independent band midpoint:

| percentile | ratio |
|---|---|
| p5 | 0.22x |
| p10 | 0.29x |
| p25 | 0.50x |
| **p50** | **0.69x** |
| p75 | 0.96x |
| p90 | **1.31x** |
| p95 | 1.54x |

**p90 / p10 = 4.5x. Stdev 0.393 on a mean of 0.738.**

The engine is not uniformly low — it is *scattered*. It over-values the top
decile by 31% while under-valuing the bottom decile by 71%.

### The decisive number

The **best possible constant multiplier** (×1.50) puts ~27 of 72 names inside a
±18% band. Shipped is ~11. **No level correction of any kind can exceed ~27/72**,
because the error is dispersion, not offset. Chasing the remaining 30% of "level
gap" has a hard ceiling well short of the goal.

## What was ruled out, and how

| candidate | verdict | evidence |
|---|---|---|
| blend clamp `[0.6,1.6]` | **ruled out** | removing it entirely rescues **zero** names (3/21 either way) |
| WACC too high | **ruled out** | engine CoE **12.2%** / WACC **11.5%** vs the independent valuers' own stated **12.0%** / **11.0%** (n=257 parsed from their working). Cutting 2pp would put the engine *below* the analysts it is measured against |
| terminal growth | ruled out | +1pp moves the median 0.024. Near value-neutral by construction — Gordon with reinvestment at g/ROIC self-cancels |
| reinvestment rate | ruled out | −10pp moves the median 0.011. TV is 79% of EV, so explicit-period FCFF barely matters |
| blend weights | ruled out | sweeping primary 0.65 → 0.35 lifts the median 0.682 → 0.745 but **in-band never improves** (9–11 throughout) while `above` creeps up. A uniform lift, not better accuracy |

## Where the dispersion actually lives

**By beta — monotonic and severe:**

| beta | n | median ratio |
|---|---|---|
| < 0.9 | 33 | 0.78x |
| 0.9–1.15 | 27 | 0.56x |
| **≥ 1.15** | **12** | **0.27x** |

**By archetype:**

| archetype | n | median | p90/p10 spread |
|---|---|---|---|
| IT_SERVICES | 5 | **1.09x** | 1.7x |
| CHEMICALS | 5 | 0.82x | 4.3x |
| CONSUMER | 12 | 0.76x | 2.8x |
| PHARMA | 8 | 0.75x | 2.1x |
| NBFC | 5 | 0.55x | 2.3x |
| AUTO | 9 | 0.53x | 2.8x |
| **CAPITAL_GOODS** | **14** | **0.47x** | **3.6x** |

The engine is **well calibrated on IT services** (1.09x, tightest spread) — a
stable, asset-light, predictable archetype — and fails hardest on
**capital-intensive, high-beta, high-growth** businesses.

## The structural reason

Of 139 independent valuations, only **46 run a DCF at all**. 119 use an earnings
multiple, 82 a cash-flow leg, 67 book/NAV. The engine weights a 10-year FCFF DCF
with a Gordon terminal at **0.65**.

Measured per-leg against the same bands: the engine's **DCF leg is 0.51x**, its
**P/E leg 0.72x**, its **exit multiple 0.81x**. *It leans hardest on its own
least accurate leg* — but re-weighting only shifts the level, because all three
legs inherit the same per-name inputs.

## What this means for the roadmap

1. **Stop hunting a global parameter.** Five have now been tested and ruled out
   on evidence. The ceiling on that whole approach is ~27/72.
2. **The tractable target is the archetypes that fail**, chiefly CAPITAL_GOODS
   (n=14, 0.47x, 3.6x spread), AUTO and NBFC — not the universe.
3. **High beta is the sharpest single marker** (≥1.15 → 0.27x). Whether beta is
   the *cause* or merely a proxy for "capital-heavy mid-cap mid-ramp" is the next
   question worth answering, and it is answerable with the data in hand.
4. **Consider whether the DCF should lead at all for these archetypes.** The
   independent valuers largely did not use one, and the engine's own multiple
   legs track their bands better than its DCF does.

## Honest note on what shipped before this

The growth/reinvestment coherence fix (PR #115) repaired a genuine incoherence —
charging measured ramp capex while crediting only capped growth — and is
defensible on its own terms. It moved the median 0.656 → 0.692. It was never
going to close a dispersion problem, and this document is the reason why.
