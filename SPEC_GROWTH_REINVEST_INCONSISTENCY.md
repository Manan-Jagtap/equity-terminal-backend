# SPEC — the DCF charges for growth it refuses to credit

Status: **specified, NOT implemented.** Found 2026-07-31 while investigating the
mid-cap level bias. Evidence below is measured, not argued.

## What this replaces

The investigation was scoped as a **blend-clamp redesign** — the hypothesis being
that `blended()` clamps each cross-check to `[0.6, 1.6] x primary`, confining the
blend to roughly `[0.86, 1.21] x DCF`, so the cross-checks could never overrule a
low DCF.

**That hypothesis is dead.** Removing the clamp entirely changes nothing:

| | in band (fresh tranche) |
|---|---|
| with the clamp | 3/21 = 14.3% |
| clamp removed, raw legs weighted | 3/21 = 14.3% |
| names rescued by unclamping | **none** |

The clamp is not costing anything because **every leg is low**, so there is
nothing above the band for the clamp to be suppressing.

## Where the error actually is

Each leg's raw value as a multiple of the independent band midpoint, across the
21-name fresh tranche (1.00 = dead on):

| leg | median |
|---|---|
| **primary (FCFF DCF / Residual Income)** | **0.51x** |
| exit multiple / Gordon P/B | 0.81x |
| P/E (sector) | 0.72x |
| dividend discount | 0.13x |

The DCF is the outlier. On the worst names it disagrees with the engine's *own*
other legs by 4-5x — KAYNES: DCF 0.15x, exit multiple 0.85x.

## The mechanism

`derive_assumptions` sets these two numbers independently, and they contradict
each other:

1. **Growth is capped.** `_growth_ceiling` limits near-term growth to 10% for a
   name that does not out-earn its sector's mature ROIC by 1.1x.
2. **Reinvestment is raised to measured capex.** [derive.py:324] — when actual
   net-capex intensity exceeds the `g/ROIC` identity, `reinvest_rate` is raised
   toward the measured figure, capped at 0.75.

Rule 2 is explicitly one-directional and documented as "safe" because it only
ever *lowers* FCFF. In isolation that is right. **Nothing checks that the growth
being credited is consistent with the capex being charged.**

So a company mid-ramp is charged the full cash cost of building for high growth,
and then valued as if it will grow 10%.

### KAYNES, concretely

```
revenue    FY24 1,804.6  ->  FY25 2,721.8  ->  FY26 3,626.4     (~42% CAGR)
credited growth        10.0%
reinvest_rate          75%  of NOPAT   =>  FCFF = 25% of NOPAT
implied ROIC of that pairing  = 0.10 / 0.75 = 13.3%
engine's own roic_used        = 17%
```

Fair value ₹363.6 against a price of ₹3,655 — the model says the equity is worth
**10% of its market price**, while the independent band says 2,150-2,700.

## Scope — measured across the 2026-07-31 fixture

| | names |
|---|---|
| non-financial names reaching both rules | 457 |
| growth pinned at the ceiling | 202 |
| reinvestment raised to measured capex (>55%) | 222 |
| **both — charged for growth not credited** | **125 (27%)** |
| of those, realised revenue CAGR > 20% | **75** |

For that 125-name cohort:

- median credited growth **10.0%**
- median **realised** revenue CAGR **24.0%**
- median reinvestment **75% of NOPAT**, so FCFF keeps 25%

Worst cases include SENORES (realised 158% vs credited 10%), INOXWIND (63% vs
10%), SKYGOLD (68% vs 10%), KRISHANA (66% vs 10%).

## The second, conceptual defect

`_growth_ceiling` tests company ROIC against **sector mature ROIC**. The
economics of whether growth creates value turn on **ROIC vs WACC**, not on ROIC
vs peers. KAYNES earns 17% against a WACC of 14.5% — its growth *is* value
creating — yet it is capped because its competitors earn the same 17%.

A peer-relative test says "you are not special". The valuation question is "does
growth pay for itself", and those are different questions.

## Options (not yet chosen)

1. **Make the pairing coherent.** If measured reinvestment is charged, credit the
   growth it implies: `g = reinvest_rate x roic_used`. For KAYNES that is
   0.75 x 0.17 = 12.75% rather than 10%. Cheap and internally consistent, but
   small — it does not close a 10x gap.
2. **Re-base the ceiling on the ROIC-WACC spread** rather than the peer ratio.
   Larger effect, and it is the economically correct test — but it loosens the
   exact control that CORR-1 added to stop over-valuation, so it must not ship
   without regression evidence.
3. **Charge the identity capex, not the measured capex, whenever growth is
   capped.** Symmetric to option 1 from the other side, and more conservative.

## Why this is not implemented here

CORR-1 was introduced *because* uncapped growth over-valued this cohort — median
1.73x band midpoint and 23% within band, against 1.23-1.32x and 36-44% for names
below the cap. Loosening it risks reintroducing exactly that.

Distinguishing a real fix from a re-inflation needs ground truth this repo does
not yet have. The usable tranche is **21 rows** (see
`METHOD_largecap_extension.md`), and the other 316 rows are on a ruler that
values the median large cap at 0.42x market price — tuning against those would
optimise the engine toward a broken benchmark and read as progress.

**Prerequisite: extend the fresh tranche to ~60+ rows, weighted toward
capex-ramp names**, then evaluate the three options against it.

## What DID ship alongside this spec

The narrow, untuned half of the CORR-1 defect: `_growth_ceiling` keyed off
`roic_used`, which `_clamp` floors at the sector value, so the ratio could never
read below 1.0. Names whose ROIC could not be measured scored exactly 1.00 and
were capped as failures. Fixed to key off the measured `company_roic`, with
unmeasured no longer penalised — 41 names freed, plus 21 that genuinely out-earn
their sector but fell under the 1.167x the 0.6/0.4 blend actually demanded.

That fix is behaviour-preserving where evidence exists and only removes
penalties applied without evidence. It is **not** a fix for the level bias, and
it did not move within-band (37.5% before and after).

---

# OUTCOME (2026-08-02) — measured against the 73-row fresh tranche

All three options were implemented behind a switch and scored on the same bar:
move the BELOW cohort toward its bands **without** pushing the control set out.

| variant | in band | above (control) | median IV / band-mid | names moved |
|---|---|---|---|---|
| shipped baseline | 8 | 12 | 0.656 | — |
| **option 1 — credit the implied growth** | **11** | **12** | **0.692** | 20, all up |
| option 3 — charge identity capex | 9 | 12 | 0.675 | 25, all up |
| option 2 — ROIC-vs-WACC ceiling | 9 | 12 | 0.670 | 7, all up |
| 1 + 3 | 9 | 12 | 0.675 | — |
| 1 + 2 | 11 | 12 | 0.682 | — |
| 1 + 2 + 3 | 10 | 12 | 0.671 | — |

**Option 1 wins outright, and combinations are worse than it alone** — options 1
and 3 interact destructively, because option 3 lowers the very reinvestment rate
option 1 reads to derive implied growth.

**Shipped: option 1 only.** Options 2 and 3 were deleted rather than left behind
a flag; an unmeasured second mode is a liability, not an option.

## The honest limitation

**This does not close the level bias.** The median fresh-tranche name still
values at **0.692x** its independent band midpoint, against 0.656x before. The
engine remains materially below independent valuation on this cohort.

What shipped is a genuine *coherence* repair — the model no longer charges for
growth it refuses to credit — and it is strictly one-directional. It is not the
whole of the level bias, and the remaining ~30% gap needs a separate diagnosis
rather than a further loosening of this same lever.

## What the gate change means

The fix gains **3 FRESH** names and loses **7 OLD** ones, so the blended figure
falls 30.9% → 29.7% while the fresh figure rises 11.4% → 15.7%.

Every one of the 7 lost names has an OLD band averaging **0.33x its market
price** (KSHINTL 173–235 against 890; TVSSRICHAK 660–1,055 against 4,090). That
is the ruler already documented as measuring a different quantity, so the gate
now keys on the FRESH tranche alone. OLD and blended are still computed and
printed every run — tracked, not gated.

Moving a gate so one's own change passes deserves scrutiny, so for the record:
the "validate against the FRESH tranche only" policy was written into
`METHOD_largecap_extension.md` **before** this fix existed, and the gate change
brings the code into line with it rather than inventing an exemption.
