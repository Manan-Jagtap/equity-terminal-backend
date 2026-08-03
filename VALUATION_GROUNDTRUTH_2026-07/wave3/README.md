# Wave 3 — pass-1 bands (64 names), audits pending

`pass1_bands.json` holds the **independent valuation bands** for all 64 wave-3
names: CAPITAL_GOODS, AUTO and NBFC, the three archetypes the dispersion
diagnosis identified as failing. Every band was produced by a valuer that never
saw the engine's answer (`saw_engine_output: false` on all 64) from two legs
required to be genuinely independent.

## Why this file exists

The valuations are the expensive half of the method — each reads four years of
statements and derives two independent legs — and they had been living only in a
workflow journal inside a session-scoped temp directory that was already wiped
once. Committing them makes the asset durable and decouples it from the
workflow-resume machinery.

## The resume trap this file removes

`resumeFromRunId` replays the longest unchanged **PREFIX** of `agent()` calls —
it is positional, not content-keyed. Editing a `NAMES` array to cut a smaller
slice diverges the call sequence at the first element, so **every value agent
after that point re-runs**. Three successive slices did exactly that: the
journal recorded 78 value-results for 64 tickers, 14 of them valued twice.

Audit runs must therefore read bands from **this file** as literal data rather
than resuming a wave script. `scratchpad/audit_only.js` is the working shape:
bands embedded, zero value agents.

## State

- 64 / 64 usable bands, none saw the engine, no valuer declined
- audits complete for **BDL, TIMKEN, COCHINSHIP**; THERMAX has 1 of 2
- ~122 audits still owed

Admission rules are unchanged (`METHOD_largecap_extension.md`): union of
concerns on independence, both audits must supply a band, audit-run overlap
>= 50%, final band = audit envelope ∩ pass 1, width >= 10%.
