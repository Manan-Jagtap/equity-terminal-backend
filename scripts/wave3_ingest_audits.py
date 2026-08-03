#!/usr/bin/env python3
"""Ingest wave-3 audit results, apply the admission gates, update the tranche.

    python3 scripts/wave3_ingest_audits.py results.json

`results.json` is {ticker: [audit1, audit2]} where each audit is the JSON the
audit prompt asks for. Everything below is the admission rule as documented in
METHOD_largecap_extension.md — kept in one place so a batch run cannot quietly
apply a looser version of it.

The rules, and why each exists:

  * BOTH audits must be present. One pass cannot measure reproducibility.
  * UNION OF CONCERNS on independence: drop if ANY run flags non-independence.
    Monotone — it can only remove rows, never add one back on a re-roll.
  * BOTH audits must supply a band. Filtering nulls out of the numerator while
    still averaging width over both audits once manufactured a 200% overlap on
    a single usable band, which passed a gate whose only purpose is to require
    two.
  * Audit-run overlap >= 50%. Calibrated against a 19-name reproducibility
    study whose observed range was 54-100%.
  * Final band = envelope of the audit runs INTERSECTED with pass 1, then >=10%
    wide. Narrower than that is false precision.
"""
import json
import os
import sys
import csv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GT = os.path.join(ROOT, "VALUATION_GROUNDTRUTH_2026-07")
BANDS = os.path.join(GT, "wave3", "pass1_bands.json")
TARGETS = os.path.join(GT, "calibration_targets.csv")
OVERLAP_MIN = 50.0
WIDTH_MIN = 10.0

CAUSE = ("independent {date} (multi-agent, financials only): two genuinely independent legs "
         "plus TWO adversarial audit passes; none of the three passes saw the engine's answer. "
         "Bands carried as committed data (wave3/pass1_bands.json), audits run standalone — no "
         "workflow resume, so no value agent re-ran. UNION OF CONCERNS; band = envelope of both "
         "audit runs intersected with pass 1; both audits must supply a band; audit-run overlap "
         "{ov:.0f}%. pass1 {p1}, audits {ab}. band {lo}-{hi} vs price {px}.")


def judge(band, audits):
    """-> (admitted_row | None, reason_if_dropped)"""
    if len(audits) < 2:
        return None, f"only {len(audits)} audit pass"
    if any(a.get("methods_truly_independent") is False for a in audits):
        return None, "dependence flagged"
    los = [a.get("suggested_lo") for a in audits]
    his = [a.get("suggested_hi") for a in audits]
    if any(v is None for v in los + his):
        return None, "an audit returned no band - reproducibility unmeasurable"
    ov = max(0.0, min(his) - max(los))
    w = sum(h - l for l, h in zip(los, his)) / len(audits)
    pct = min(100.0, (ov / w) * 100) if w > 0 else 0.0
    if pct < OVERLAP_MIN:
        return None, f"audit runs overlap {pct:.0f}% < {OVERLAP_MIN:.0f}%"
    lo = max(band["target_lo"], min(los))
    hi = min(band["target_hi"], max(his))
    if not hi > lo:
        return None, "pass1 misses the audit envelope"
    width = (hi - lo) / ((hi + lo) / 2) * 100
    if width < WIDTH_MIN:
        return None, f"final band {width:.0f}% wide < {WIDTH_MIN:.0f}%"
    return {"lo": round(lo, 1), "hi": round(hi, 1), "overlap": pct,
            "pass1": [band["target_lo"], band["target_hi"]],
            "audit_bands": [[l, h] for l, h in zip(los, his)]}, None


def main():
    import datetime as dt
    results = json.load(open(sys.argv[1]))
    d = json.load(open(BANDS))
    bands, done = d["bands"], d.get("audits_done", {})
    flagged = set(d.get("dependence_flagged", []))
    rows = list(csv.DictReader(open(TARGETS)))
    hdr = list(rows[0].keys())
    by = {(r["ticker"] or "").upper(): r for r in rows}
    date = dt.date.today().isoformat()

    admitted, dropped = [], []
    for tk, auds in results.items():
        b = bands.get(tk)
        if not b:
            dropped.append((tk, "no pass-1 band")); continue
        row, why = judge(b, auds)
        done[tk] = max(done.get(tk, 0), len(auds))
        if row is None:
            dropped.append((tk, why))
            if why == "dependence flagged":
                flagged.add(tk)
            continue
        rec = {"ticker": tk, "archetype": b.get("archetype") or "",
               "eng_verdict": "", "eng_mos": "",
               "target_verdict": b.get("target_verdict") or "",
               "target_lo": str(row["lo"]), "target_hi": str(row["hi"]),
               "vintage": "FRESH",
               "cause": CAUSE.format(date=date, ov=row["overlap"], p1=row["pass1"],
                                     ab=row["audit_bands"], lo=row["lo"], hi=row["hi"],
                                     px=b.get("price"))}
        if tk in by:
            by[tk].update(rec)
        else:
            rows.append(rec)
        admitted.append((tk, row["lo"], row["hi"], row["overlap"]))

    with open(TARGETS, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=hdr)
        w.writeheader(); w.writerows(rows)
    d["audits_done"] = done
    d["dependence_flagged"] = sorted(flagged)
    json.dump(d, open(BANDS, "w"), indent=1, sort_keys=True)

    fresh = sum(1 for r in rows if r.get("vintage") == "FRESH")
    print(f"admitted {len(admitted)} / {len(results)}")
    for tk, lo, hi, ov in admitted:
        print(f"  + {tk:12} {lo}-{hi}  overlap {ov:.0f}%")
    for tk, why in dropped:
        print(f"  - {tk:12} {why}")
    left = [t for t in bands if done.get(t, 0) < 2 and t not in flagged]
    print(f"\nFRESH tranche now {fresh}; wave-3 names still un-audited: {len(left)}")


if __name__ == "__main__":
    main()
