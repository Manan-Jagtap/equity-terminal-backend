# Track-record study — do the engine's calls predict anything yet?

Run 2026-08-02 against `VerdictSnapshot` (32,429 rows, 11 Jun – 2 Aug 2026).

## Why this was run

Every prior assessment of the engine's level bias is **model versus model**: the
73-row fresh ground-truth tranche is 73 careful opinions, the engine is one
opinion. Neither is an outcome. Before pushing valuations further up, it was
worth asking whether realised prices agree with either.

## Method

For each name, take the **first** verdict in the window and the price that day,
then measure the return to the last observation. First-verdict-only avoids
look-ahead. Names need ≥10 snapshots and a ≥21-day window.

**n = 503 names, median holding window 52 days.**

## Result 1 — verdicts show no positive discrimination

| first verdict | n | median return | % positive |
|---|---|---|---|
| BUY | 41 | **+2.08%** | 65.9% |
| ACCUMULATE | 36 | +2.65% | 66.7% |
| HOLD | 40 | +3.19% | 62.5% |
| **REDUCE** | 41 | **+6.50%** | 68.3% |
| AVOID | 288 | +3.26% | 58.3% |
| LOW CONF | 38 | +5.89% | 76.3% |
| **ALL** | **503** | **+3.38%** | |

**Bullish minus bearish spread: −0.72 pp.** The engine's BUY/ACCUMULATE names
*underperformed* its AVOIDs. REDUCE was the best-performing directional bucket.

## Result 2 — margin of safety carries no signal

| MoS quintile | n | median return |
|---|---|---|
| Q1 (−96%..−69%, most bearish) | 96 | +1.36% |
| Q2 (−69%..−53%) | 96 | +4.16% |
| Q3 (−53%..−32%) | 97 | **+5.57%** |
| Q4 (−32%..+6%) | 96 | +4.76% |
| Q5 (+9%..+849%, most bullish) | 97 | +2.55% |

**Spearman rank correlation, MoS vs realised return: +0.037.** Zero.
High-confidence subset (n=454): **+0.041**. Also zero. The relationship is
hump-shaped, not monotonic — the extremes at both ends did worst.

## What this does and does not establish

**It does NOT show the engine is worthless.** 52 days is far too short to judge
a valuation model; the convention is one to three years. The window is also a
single rising regime (+3.38% median across everything), which flatters and
compresses everything, and n=41 on BUY is thin.

**It does establish one thing that matters right now:** the engine's bearish
tilt is **not being vindicated by outcomes**. It calls 46% of the universe AVOID
and those names returned +3.26% against a +3.38% universe median — in line, not
falling.

## Consequence for the level-bias work

The independent ground truth says the engine values the median fresh name at
0.692x its band. The engine's own defence would be "the market is expensive and
I am right". **Outcomes do not support that defence** — they are neutral on it
at worst.

So the level bias should be treated as **real**, and the work in
`SPEC_GROWTH_REINVEST_INCONSISTENCY.md` should continue rather than stop. That
was a genuine fork: had AVOIDs materially underperformed, the correct response
would have been to STOP lifting valuations.

## Consequence for the product

The public track record currently shows **no demonstrated edge**. That is a
material fact for a commercial research product and must not be presented
otherwise. Per the append-only ledger doctrine this is recorded as found —
neither buried nor explained away — and re-run as the window lengthens, when it
will carry far more weight than it does at 52 days.
