# SPEC — DAT-13 alternative: keep the AVOID, suppress the number

Status: **proposed, not implemented.** Shipped behaviour today (PR #81) is the
LOW CONF abstention. This spec describes the alternative and what it costs.

## The problem with what shipped

62 names value below 10% of price. PR #81 turns all of them into LOW CONF.
That is honest about the *number* but throws away the *direction* — and the
direction was usually right: **23 of them previously agreed with independent
ground truth**, and now say nothing. We suppressed a correct call to avoid
publishing an indefensible price target.

The two claims are separable:

| claim | supportable at mos ≤ −0.90? |
|---|---|
| "this equity is worth ₹3.1" | **no** — thin residual behind heavy debt; a 5% EV error moves it 10x |
| "this is not investable at ₹1,380" | often **yes** — and ground truth agreed 23 times |

## Proposed behaviour

Keep the verdict. Suppress the point estimate. Never present a number the
model cannot support, but do not pretend to have no opinion.

### 1. Engine (`app/engines.py`)

Replace the verdict override in `recommend()` with a suppression flag:

```python
value_suppressed = False
if mos is not None and mos <= _COLLAPSED_MOS and verdict not in ("NO DATA", "NO CALL"):
    if _collapse_corroborated(co, a, v):
        value_suppressed = True          # keep verdict; hide the point estimate
        conf = {**conf, "level": "low", "score": min(conf.get("score") or 0.5, 0.5)}
        _gate = "value_suppressed"
        reasons.append({"label": "Fair value", "score": 35,
                        "note": "Equity value here is a thin residual behind heavy debt, "
                                "so the point estimate is not meaningful. The direction is "
                                "corroborated independently; the precise figure is not "
                                "published.", "good": False, "bad": True})
    else:
        verdict = "LOW CONF"             # uncorroborated → abstain, as today
        reliable = False
        _gate = "collapsed_value"
```

Return `"value_suppressed": value_suppressed` alongside `intrinsic` / `mos`.

**Do not null `intrinsic` in the engine.** The batch, the calibration harness
and the integrity sweep all need the raw figure. Suppression is a
*presentation* contract, enforced at the API boundary and the UI.

### 2. Corroboration — the load-bearing part

Keeping a call means staying accountable for it, so it must be earned. Require
**at least one independent leg** to agree the name is expensive:

- the relative-multiple leg (exit P/E on forward year-N metrics) also lands below price; **or**
- P/B > 3 with ROE below the sector's mature ROE (paying a premium for sub-par returns); **or**
- net debt / EBITDA > 6 (the leverage that makes equity a thin residual in the first place)

No corroboration → fall through to LOW CONF exactly as today. This is what
stops the spec becoming "publish AVOID whenever the DCF collapses", which
would be laundering a broken number into a call.

### 3. API (`app/main.py`, screener + detail payloads)

When `value_suppressed`:

- `intrinsic`, `blended`, `mos` → `null` in the public payload
- add `"fair_value_note": "not meaningful"` and keep `verdict`, `confidence`, `reasons`
- `composite` / ranking scores: computed **without** the MoS term, so a −99% MoS cannot dominate any ranking

### 4. Frontend

- Company header "Fair Value" → `n/m` with a tooltip carrying the reason string
- Verdict badge renders normally (AVOID)
- Screener: MoS column `n/m`; **sorting by MoS puts these last**, never at the top of "most overvalued"
- Charts: no intrinsic reference line
- One-pager PDF / Excel: same `n/m` treatment — no cell may carry the raw figure

### 5. Ledger and track record

These stay **gradable** — that is the whole point of keeping the verdict.
`EngineCall` records the AVOID with `value_suppressed=true` so a future review
can see the call was made without a price target. Append-only doctrine
unchanged; add a `LEDGER_NOTES` entry on the effective date.

## Expected effect

- The 23 ground-truth-agreeing AVOIDs come back → the soft-break count on the
  calibration harness should drop toward 0
- Within-band is **unaffected** either way (that metric scores intrinsics, and
  these names are out of band under both designs)
- LOW CONF count falls from 289 toward ~249 + (uncorroborated collapses)

## Risks

1. **Presentation-layer leak.** The number still exists in the engine; any
   surface that forgets the flag will print ₹3.1 again. Mitigate with one
   serializer helper used by every payload, plus a test asserting no public
   endpoint returns a non-null `mos` when `value_suppressed`.
2. **A call without a number is harder to defend to a user** ("why AVOID?").
   The `reasons` string must carry the corroboration leg that earned it.
3. **Corroboration thresholds are new free parameters.** Sweep them on the
   calibration set before shipping; do not hand-pick.

## Test plan

- End-to-end through `recommend()` (never the helper — that was the PR #80 defect)
- Corroborated collapse → verdict preserved, `value_suppressed=True`, `mos` present internally
- Uncorroborated collapse → LOW CONF, as today
- Public payload for a suppressed name → `mos is None` and `intrinsic is None`
- Screener sort by MoS → suppressed names last
- Calibration: soft breaks must fall; 0 hard breaks; within-band not regressed

## Recommendation

Worth doing. It recovers real signal that today's shipped behaviour discards,
and the corroboration requirement keeps it honest. The work is mostly at the
presentation boundary, which is also where the risk sits — one leaked surface
and we are publishing ₹3.1 again, so the serializer test is not optional.
