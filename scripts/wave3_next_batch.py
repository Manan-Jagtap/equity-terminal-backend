#!/usr/bin/env python3
"""Emit the next wave-3 audit batch — names, bands, and the audit prompt.

Runs ANYWHERE with python3 and internet: no venv, no local paths, no DB. The
bands come from the committed pass1_bands.json, which is the whole point — the
expensive pass-1 valuations are data now, so an audit batch never re-derives
them and never depends on a workflow's resume cache.

    python3 scripts/wave3_next_batch.py --n 12 > batch.json

Skips names already audited twice and names on the dependence ban list. That
ban list is MONOTONE: a name flagged once is never re-rolled, because re-rolling
until a name passes is how a benchmark gets quietly tuned toward what it grades.
"""
import argparse
import json
import os

BANDS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "VALUATION_GROUNDTRUTH_2026-07", "wave3", "pass1_bands.json")

# Portable: the public API, plain python3, no venv and no hard-coded home dir.
# The old prompt embedded `cd /Users/... && venv313/bin/python`, which is exactly
# why the audit machinery could not run anywhere but one laptop.
FETCH = """python3 -c "
import json,urllib.request
j=json.loads(urllib.request.urlopen('https://api.equityverdict.com/api/companies/{tk}',timeout=60).read())
co=j.get('company') or j
st=co.get('statements') or {{}}
out={{'price':co.get('price'),'shares_cr':co.get('shares'),'net_debt':co.get('net_debt'),
     'equity':co.get('equity'),'type':co.get('type'),'sector':co.get('sector'),
     'nbfc':co.get('nbfc'),'statements':{{y:st[y] for y in sorted(st)[-4:]}}}}
print(json.dumps(out,indent=1))" """

AUDIT_PROMPT = """Adversarially audit this independent valuation band for {tk}. Find what is WRONG; do not agree by default. But hold a MATERIALITY bar: only set sound=false for an error that would move the band, not for stylistic preference.

Band: {lo} - {hi}, verdict {verdict}, price {price}
Leg 1: {m1}
Leg 2: {m2}
Claimed independence: {indep}
Rationale: {rat}

Re-pull the financials yourself:
{fetch}

Check, in priority order:
1. ARE THE TWO LEGS ACTUALLY INDEPENDENT? If both are earnings multiples off the same year, or the ranges were chosen so they overlap, the "intersection" is fabricated confidence. Set methods_truly_independent=false. This is the most common defect.
2. Arithmetic: per-share vs total, crore units, net debt sign, market cap = price * shares_cr, and for banks/NBFCs that no EV multiple was used.
3. Are the multiple ranges defensible for THIS business, or generic?
4. Trailing figures used on a cyclical at peak or trough, or on a business mid-capex-ramp?
5. Does the verdict follow from where the price sits relative to the band?

Give suggested_lo/suggested_hi if you can do better. Be specific — "seems high" is useless.

Return STRICT JSON only, no prose around it:
{{"ticker":"{tk}","sound":<bool>,"methods_truly_independent":<bool>,"concern":"<what is wrong>","suggested_lo":<number>,"suggested_hi":<number>}}"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=12, help="names per batch (2 audits each)")
    ap.add_argument("--bands", default=BANDS)
    a = ap.parse_args()

    d = json.load(open(a.bands))
    bands = d["bands"]
    done = d.get("audits_done", {})
    banned = set(d.get("dependence_flagged", []))

    avail = [t for t in sorted(bands) if done.get(t, 0) < 2 and t not in banned]
    # Rotate archetypes so no cohort is starved: the value of the batch is
    # per-archetype leg accuracy, which needs all three to grow together.
    by = {}
    for t in avail:
        by.setdefault(bands[t]["archetype"], []).append(t)
    pick, order = [], sorted(by, key=lambda k: -len(by[k]))
    i = 0
    while len(pick) < a.n and any(by.values()):
        arch = order[i % len(order)]
        if by[arch]:
            pick.append(by[arch].pop(0))
        i += 1
        if all(not v for v in by.values()):
            break

    out = []
    for tk in pick:
        b = bands[tk]
        out.append({
            "ticker": tk,
            "archetype": b.get("archetype"),
            "prompt": AUDIT_PROMPT.format(
                tk=tk, lo=b.get("target_lo"), hi=b.get("target_hi"),
                verdict=b.get("target_verdict"), price=b.get("price"),
                m1=b.get("method_1"), m2=b.get("method_2"),
                indep=b.get("methods_are_independent"), rat=b.get("rationale"),
                fetch=FETCH.format(tk=tk)),
        })
    print(json.dumps({
        "batch": out,
        "remaining_after_batch": len(avail) - len(pick),
        "total_unaudited": len(avail),
        "note": "Run EACH prompt TWICE in SEPARATE agent contexts. Two passes from "
                "one context are not two independent samples and the >=50% "
                "audit-overlap gate becomes meaningless.",
    }, indent=1))


if __name__ == "__main__":
    main()
