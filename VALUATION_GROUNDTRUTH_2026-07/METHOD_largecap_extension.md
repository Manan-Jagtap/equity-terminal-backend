# Extending independent ground truth to the large caps — method

**Status: worklist prepared, no valuations done.** 130 names in
`uncovered_largecaps.txt`. This file exists so the work is executed the same
way the original 319 were, by someone with the context to do it properly.

## Why this is the highest-value open item

Independent ground truth covers **79 of the 209** largest companies (38%). The
uncovered 130 open with RELIANCE, BHARTIARTL, ICICIBANK, INFY — the names
users actually hold. Consequence: the engine's **−0.52 median MoS on large
caps is currently unfalsifiable.** We cannot say whether it is a correct read
of an expensive market or a residual level bias from CORR-1/6/8.

## The one rule that makes this worth doing

**Value the company before looking at what the engine said.** Every safeguard
here exists to stop the benchmark becoming a mirror.

Concretely, per name, in this order:

1. Pull the financials (`/api/companies/{ticker}` — statements, not verdicts).
2. Form a view: revenue trajectory, margin trend, capital intensity, balance
   sheet, sector position.
3. Derive a band from **at least two** methods that do NOT share our engine's
   machinery — e.g. peer-multiple on trailing/forward earnings, EV/EBITDA
   against sector comps, book-value or embedded-value anchor for financials,
   dividend capacity for utilities. Explicitly NOT: our FCFF DCF, our exit
   multiples, our sector_params.
4. Write `target_lo,target_hi` as the range you would defend, not a point
   estimate ± a tolerance.
5. **Only then** compare with the engine, and record the delta.

If step 5 changes step 4, the row is void. Note the temptation and move on.

## Guardrails

- **Never** consult `analyst_target` while forming the band. Consensus is the
  *other* oracle (`consensus_targets.csv`) and must stay uncontaminated for the
  same reason — two oracles that peeked at each other are one oracle.
- Band width should reflect genuine uncertainty. A ±5% band on a cyclical is
  false precision; a ±60% band on HINDUNILVR is an abdication.
- **Skip rather than guess.** A missing row is honest; a fabricated one
  silently corrupts every future calibration number and cannot be detected
  later. Coverage is not the goal — a trustworthy denominator is.
- Batch 15–20 names per session. Beyond that, quality decays and the later
  rows start anchoring on the earlier ones.

## Acceptance

- Append to `calibration_targets.csv` with the existing schema, one row per name.
- Re-run `tests/calibration_check.py`; **expect within-band to MOVE**, possibly
  down. A number that stays at 38.5% after adding 130 large caps means the new
  rows were drawn toward what the engine already said.
- Ratchet `calib_baseline.json` only after the new rows are reviewed.

## Why not automate it

Any band this codebase can compute is a band derived from this codebase. That
is the circularity that makes the exercise worthless — it would move the
headline up while destroying its meaning. This is research work; the engine
cannot grade its own homework.

## Reproducibility of the audit pass (measured 2026-07-31)

The batch-4 audits were re-run against the *same* pass-1 bands. Nineteen names
have two runs, which makes the audit's own reliability measurable rather than
assumed. It splits sharply in two:

| output | reproducible? | evidence |
|---|---|---|
| band endpoints | **yes** | median midpoint drift **3.6%**, max 12.1%, median band overlap 82% |
| direction vs the engine | **yes** | 18 of 19 unchanged; only BRITANNIA moved (`below` → a marginal `hit`) |
| `sound` / `methods_truly_independent` | **no** | flipped on **9 of 19** |

The categorical self-assessment is close to a coin flip on borderline names,
while the numbers it produces are stable. That matters because the original
admission gate rode entirely on `methods_truly_independent` — the one output
that does not reproduce. Two committed rows (AXISBANK, CIPLA) had been admitted
on a single favourable roll and were removed; two names dropped in batch 4
(DABUR, EICHERMOT) would have been admitted on a second roll.

### The rule that replaced it

**Union of concerns.** A name is admitted only if *no* audit run flagged
non-independence, and the band is the envelope of every audit run intersected
with pass 1.

This is deliberately **monotone: it can only ever remove rows, never add one
back on a favourable re-roll.** Re-rolling until a name passes is precisely how
a benchmark gets quietly tuned toward the thing it is meant to grade. A false
"independent" now requires *both* runs to miss the dependence, instead of one
lucky draw.

### What this does not undermine

The level-bias conclusion is unaffected: it rests on the direction of the
engine against the band, which reproduced 18 times out of 19. Large caps remain
mixed (4 above, 4 hits, 11 below across the re-run set) against the mid-caps'
10-below-out-of-10 — so the bias stays **cohort-specific**, and the blend clamp
stays the prime suspect.

### Standing requirement

**Any new ground-truth row needs two audit passes, not one.** A single pass
cannot distinguish a genuine independence defect from noise, and the whole
value of this file is that its rows were not selected for agreeing with
anything.

### Disclosure: this correction moved the headline UP, and why that is not a win

Within-band went **37.3% → 37.6%** after the correction. A method change that
flatters the engine deserves suspicion, so here is the whole of it:

| name | engine IV | change | effect |
|---|---|---|---|
| AXISBANK | 1,259.4 | row removed (band was 1,020–1,200) | removes a **miss** |
| CIPLA | 1,152.1 | row removed (band was 1,050–1,210) | removes a **hit** |
| BRITANNIA | 3,277.3 | band 3,300–3,850 → 3,250–3,900 | miss → **hit** |

Net +1 in-band on a denominator two smaller. **None of it is the engine getting
better** — it is two rows leaving and one band widening.

The BRITANNIA hit is worth naming explicitly: it clears the new floor by **₹27
on ₹3,250**, under 1%. Counted as a hit by the rule, it is a near-miss in
substance, and it would flip back on any band redraw.

The envelope rule widens bands, and wider bands are easier to land inside — the
rule is conservative about *admitting rows* but permissive about *band width*.
That asymmetry is deliberate: when two competent auditors say 3,300–3,850 and
2,900–3,900, the honest band spans both. Reporting the narrower one would be
false precision in the benchmark itself. But it does mean **within-band is not
comparable across a method change**, and the 37.6% should not be read as
progress against the 37.3%.
