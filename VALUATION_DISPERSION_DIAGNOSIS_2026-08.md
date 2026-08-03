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

---

# Addendum — per-archetype decomposition, and why parameter work stops here

## The engine weights its worst leg hardest

Per-leg accuracy against the independent band midpoint (1.00 = dead on):

| archetype | n | blend | DCF | exit | P/E | best leg |
|---|---|---|---|---|---|---|
| CAPITAL_GOODS | 14 | 0.47 | **0.39** | 1.18 | **0.96** | P/E |
| CONSUMER | 12 | 0.76 | 0.64 | 1.51 | 1.18 | P/E |
| AUTO | 9 | 0.53 | 0.44 | 0.96 | 1.05 | exit |
| PHARMA | 8 | 0.75 | 0.69 | 0.99 | 1.12 | exit |
| IT_SERVICES | 5 | 1.09 | **0.88** | 1.32 | 1.39 | **DCF** |
| CHEMICALS | 5 | 0.82 | 0.73 | 1.35 | 1.09 | P/E |
| NBFC | 5 | 0.55 | 0.53 | — | 0.75 | P/E |
| METAL | 4 | 0.73 | 0.67 | 0.74 | 0.99 | P/E |
| **ALL** | | | **0.65** | **1.18** | **1.07** | |

**Shipped weights are DCF 0.65 / exit 0.20 / P/E 0.15** — 65% on the least
accurate leg, 15% on the most accurate. IT_SERVICES is the sole archetype where
the DCF is the best leg, and it is also the only archetype the engine values
correctly overall (1.09x).

## Two candidate fixes, both tested and both rejected

**Re-weight toward P/E.** Driving the DCF to 0.25 and P/E to 0.60 lifts the
median 0.675 → 0.771 and in-band 11 → 14 — but **dispersion barely moves (4.4x →
4.0x)** and `above` rises 10 → 12. It is another level shift dressed as an
accuracy fix, and it would gut a DCF-led engine on the evidence of 72 names.

**Conservative variant** — hold the DCF at 0.65 and move weight from exit (1.18x,
inflated) to P/E (1.07x) — yields **at most +1 in-band** with no dispersion
change and no median improvement. Noise at this sample size.

## A clean hypothesis, refuted

A DCF is a *measurement* when the explicit period carries the value and an
*extrapolation* when the terminal does — so DCF accuracy should decay as
`tv_share` rises, and `tv_share` is already computed. **The data says the
opposite:**

| tv_share | n | DCF accuracy |
|---|---|---|
| <65% | 16 | 0.58x |
| 65–75% | 13 | **0.34x** |
| 75–85% | 19 | 0.51x |
| **≥85%** | 17 | **0.84x** |

Spearman(tv_share, DCF accuracy) = **+0.356** — accuracy *improves* with terminal
share, and the buckets are non-monotonic besides. **There is no per-name signal
that identifies when the DCF can be trusted.** It is unreliable everywhere, while
P/E is stable at 1.03–1.17x across every bucket.

## Why parameter work stops here

Seven levers have now been tested and rejected on evidence: blend clamp, WACC,
terminal growth, reinvestment, blend weights (twice), per-name beta, and
tv_share-conditional weighting. Each either shifts the level without improving
accuracy, or lands inside noise on n=72.

**The 73-row tranche is exhausted.** Archetype cells are 4–14 names; any further
tuning is fitting to individual companies. The two honest ways forward:

1. **More ground truth** — 25–30 rows per failing archetype (CAPITAL_GOODS, AUTO,
   NBFC) would make archetype-level conclusions statistically real rather than
   suggestive.
2. **A different class of intervention** — the evidence points at *per-archetype
   models*, not per-archetype constants. CAPITAL_GOODS at DCF 0.39x / P/E 0.96x
   is not a mis-set parameter; it is a business type the FCFF construction does
   not describe well.

Neither is a parameter change, and pretending otherwise would produce a number
that looks better on 72 names and is worse everywhere else.

---

# WAVE 3 COMPLETE (2026-08-03) — the archetype signals held

All 64 wave-3 names audited. Fresh tranche **105 rows**. The three target
archetypes grew enough to make per-archetype leg accuracy testable rather than
suggestive:

| archetype | n before → after | DCF | exit | P/E | best leg |
|---|---|---|---|---|---|
| **CAPITAL_GOODS** | 14 → **22** | **0.41** | 1.29 | **0.99** | P/E |
| **AUTO** | 9 → **20** | 0.50 | **1.16** | 1.19 | **exit** |
| **NBFC** | 5 → **17** | 0.57 | — | **0.86** | P/E |
| IT_SERVICES | 5 | **0.88** | 1.32 | 1.39 | **DCF** |
| **ALL** | 104 | **0.65** | **1.29** | **1.10** | |

Engine weights remain **DCF 0.65 / exit 0.20 / P/E 0.15** — still heaviest on
the least accurate leg, now measured on 104 rows rather than 72.

## What survived the larger samples

**CAPITAL_GOODS DCF is immovable: 0.39 → 0.39 → 0.41** across n=14, 18 and 22,
against a P/E leg that is essentially exact (0.96 → 0.99 → 0.99). Three
independent sample sizes, same answer. The FCFF construction does not describe
capital-intensive businesses, and this is now the hardest single finding in the
investigation.

**AUTO's anomaly held.** It is the only archetype where the EXIT multiple beats
P/E, and that survived more than doubling the sample (n=9 → 20). Every other
cohort prefers P/E. That single fact is what distinguishes "re-weight globally"
from "weight per archetype" — and it is the evidence a global re-weight never
had.

**NBFC improved but stays worst.** DCF 0.53 → 0.57, P/E 0.75 → 0.86 at n=17.
The residual-income path fails in the *same direction* as FCFF, just less
severely. That localises nothing: the cause sits upstream of both models, in
shared inputs.

**IT_SERVICES remains the sole archetype the DCF describes** (0.88, best leg)
and the only one the engine values correctly overall (1.09).

## Dispersion, at n=104

p90/p10 **3.9x** (was 4.5x at n=72); median IV/band-mid **0.675**. Engine
against the bands: **76 below, 15 in band, 13 above**.

Dispersion narrowed slightly with more data but the diagnosis is unchanged — the
error is scatter, not offset, and no global lever addresses scatter.

## What the wave cost, and the gates that earned it

64 names valued, ~190 audit agents. **32 names admitted, 26 dependence-flagged,
plus a handful on band width or audit-run overlap.** Roughly 45% of everything
audited was rejected — nearly all of it because two supposedly independent legs
turned out to be one method restated. Those 26 rows would have entered the
benchmark looking like corroboration.

The dependence rate rose as batches reached smaller, thinly-disclosed companies
(50% → 58% → 20% → 40% → 64% admit across batches), which is why the wave
yielded ~32 rows rather than the 64 it was sized for. Stated at the time rather
than presented as a shortfall afterwards.
