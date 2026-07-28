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
