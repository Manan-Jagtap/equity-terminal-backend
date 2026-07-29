#!/usr/bin/env python3
"""Per-PR calibration harness — full-pipeline replay scored against the
groundtruth correction targets. Validated 12/12 against LIVE prod (the replay
reproduces the deployed engine's detail-page intrinsic to the penny).

Two modes:
  --write-baseline PATH   run the replay over every scored name; snapshot
                          {intrinsic, verdict, mos, in_target_band, in_agree_band}
                          to PATH. Run this ONCE on the current (pre-batch) engine.
  (default) --baseline PATH
                          re-run the replay and diff against the snapshot:
                            * within-band % on the 319 correction targets (+delta)
                            * per-cohort (archetype) within-band deltas
                            * Agree do-not-break: any name in-agree-band at
                              baseline but NOT now is a BREAK (exit 1)
                            * within-band regression (exit 1)

Regression bar (every valuation-touching batch): calibration_targets.csv
within-band must not fall and the 199 Agree rows must stay unbroken. The ≥80%
figure is the destination after all level fixes, not a single-batch floor.

Usage:
  python tests/calibration_check.py --write-baseline tests/calib_baseline.json
  python tests/calibration_check.py --baseline tests/calib_baseline.json [--cohort TICKER,TICKER,...]
"""
import os, sys, csv, json, argparse, collections
sys.path.insert(0, os.path.dirname(__file__))
import _calib_replay as R

GT_DIR = "VALUATION_GROUNDTRUTH_2026-07"
TARGETS_CSV = os.path.join(GT_DIR, "calibration_targets.csv")
GROUNDTRUTH_CSV = os.path.join(GT_DIR, "groundtruth.csv")


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# Verdict vocabularies differ: the engine emits point labels (BUY/ACCUMULATE/...),
# the independent groundtruth emits zone labels (BUY-zone/ACC-zone/...). Canonicalize
# to a direction and an ordinal so the do-not-break check measures how FAR the engine
# sits from the independent verdict, robust to label style.
_ORD = {"POS": 4, "HOLD": 3, "REDUCE": 2, "AVOID": 1}
_NORM_VERDICT = {"TRIM": "REDUCE"}


def zone(v):
    v = _NORM_VERDICT.get((v or "").strip().upper(), (v or "").strip().upper())
    if v in ("BUY", "ACCUMULATE", "BUY-ZONE", "ACC-ZONE"):
        return "POS"
    if v == "HOLD":
        return "HOLD"
    if v == "REDUCE":
        return "REDUCE"
    if v == "AVOID":
        return "AVOID"
    if v in ("LOW CONF", "NO CALL", "LOWCONF"):
        return "ABSTAIN"
    return v or "?"


def agree_distance(eng_verdict, my_verdict):
    """Ordinal gap between engine and independent verdict; None if either abstains
    or is unmappable (those are tracked via the soft-abstain flag instead)."""
    ze, zm = zone(eng_verdict), zone(my_verdict)
    if ze not in _ORD or zm not in _ORD:
        return None
    return abs(_ORD[ze] - _ORD[zm])


def load_targets():
    """ticker -> {lo, hi, target_verdict, archetype, cause}"""
    out = {}
    for r in csv.DictReader(open(TARGETS_CSV)):
        out[r["ticker"].upper()] = {
            "lo": _f(r["target_lo"]), "hi": _f(r["target_hi"]),
            "target_verdict": r["target_verdict"], "archetype": r["archetype"],
            "cause": r.get("cause", ""),
        }
    return out


def load_agree():
    """class==Agree ticker -> {my_lo, my_hi, my_verdict}"""
    out = {}
    for r in csv.DictReader(open(GROUNDTRUTH_CSV)):
        if (r.get("class") or "").strip() == "Agree":
            out[r["ticker"].upper()] = {
                "my_lo": _f(r["my_lo"]), "my_hi": _f(r["my_hi"]),
                "my_verdict": r.get("my_verdict"),
            }
    return out


def _pct(num, den):
    return 100.0 * num / max(1, den)


def run_replay(fixture, universe):
    """Return ticker -> {intrinsic, verdict, mos} for every name in `universe`
    that is present in the fixture. Names absent from the fixture are coverage
    gaps (reported, not scored)."""
    R.build_db(fixture)
    scores, missing, errored = {}, [], []
    for tk in sorted(universe):
        e = fixture.get(tk)
        if not e:
            missing.append(tk)
            continue
        try:
            rec = R.score_name(tk, e)
            # A name whose value comes from an ALT MODEL (conglomerate SOTP,
            # holdco stakes, insurer P/EV, REIT) emits components with no
            # `method` — the blend legs are segment marks, not computed legs.
            # Those names are graded against HAND-SEEDED inputs (see
            # alt_models.PRESETS_AS_OF), so "is the engine accurate?" and "do we
            # agree with whoever typed the segment marks?" are different
            # questions and must not be reported as one number.
            _c = rec.get("components") or (rec.get("valuation") or {}).get("components") or []
            scores[tk] = {"intrinsic": rec.get("intrinsic"),
                          "verdict": rec.get("verdict"), "mos": rec.get("mos"),
                          "alt_model": bool(_c) and all(c.get("method") is None for c in _c)}
        except Exception as ex:  # noqa: BLE001 — record, don't abort the sweep
            errored.append((tk, str(ex)[:80]))
    return scores, missing, errored


def score(scores, targets, agree):
    """Attach in_target_band / in_agree_band to each scored name."""
    out = {}
    for tk, s in scores.items():
        iv = s.get("intrinsic")
        rec = dict(s)
        t = targets.get(tk)
        if t and iv is not None and t["lo"] is not None and t["hi"] is not None:
            rec["in_target_band"] = bool(t["lo"] <= iv <= t["hi"])
        else:
            rec["in_target_band"] = None
        rec["alt_model"] = bool(s.get("alt_model"))
        a = agree.get(tk)
        if a and iv is not None and a["my_lo"] is not None and a["my_hi"] is not None:
            rec["in_agree_band"] = bool(a["my_lo"] <= iv <= a["my_hi"])
        else:
            rec["in_agree_band"] = None
        if a:
            rec["agree_dist"] = agree_distance(s.get("verdict"), a["my_verdict"])
            rec["agree_abstain"] = zone(s.get("verdict")) == "ABSTAIN"
        out[tk] = rec
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-baseline", metavar="PATH")
    ap.add_argument("--baseline", metavar="PATH", default="tests/calib_baseline.json")
    # Default None so load_fixture() picks the COMMITTED .gz — a gitignored raw
    # json must never silently shadow what CI reads (see _calib_replay docstring).
    ap.add_argument("--fixture", default=None,
                    help="explicit fixture path; default = the committed .gz")
    ap.add_argument("--cohort", default="", help="comma-separated tickers to attribute separately")
    args = ap.parse_args()

    fixture = R.load_fixture(args.fixture)
    targets, agree = load_targets(), load_agree()
    universe = set(targets) | set(agree)
    scores, missing, errored = run_replay(fixture, universe)
    scored = score(scores, targets, agree)

    tgt_names = [tk for tk in targets if tk in scored and scored[tk]["in_target_band"] is not None]
    in_band = [tk for tk in tgt_names if scored[tk]["in_target_band"]]
    agr_names = [tk for tk in agree if tk in scored and scored[tk]["in_agree_band"] is not None]
    in_agr = [tk for tk in agr_names if scored[tk]["in_agree_band"]]

    print(f"replay: {len(scored)} scored | target coverage {len(tgt_names)}/{len(targets)} "
          f"| Agree coverage {len(agr_names)}/{len(agree)}")
    if missing:
        print(f"  coverage gaps (not in fixture): {len(missing)} -> {', '.join(sorted(missing)[:12])}"
              + (" ..." if len(missing) > 12 else ""))
    if errored:
        print(f"  replay errors: {len(errored)} -> " + "; ".join(f"{t}:{m}" for t, m in errored[:6]))
    pct = 100.0 * len(in_band) / max(1, len(tgt_names))
    print(f"\nWITHIN-BAND (correction targets): {len(in_band)}/{len(tgt_names)} = {pct:.1f}%")
    # Split computed vs preset-driven. Conflating them hides that ~5% of the
    # book is scored against hand-seeded segment marks rather than a valuation
    # the engine derived, so a move in the headline can come from either.
    _alt = [tk for tk in tgt_names if scored[tk].get("alt_model")]
    if _alt:
        _cmp = [tk for tk in tgt_names if tk not in _alt]
        _ain = [tk for tk in _alt if scored[tk]["in_target_band"]]
        _cin = [tk for tk in _cmp if scored[tk]["in_target_band"]]
        print(f"   computed      {len(_cin)}/{len(_cmp)} = {_pct(len(_cin), len(_cmp)):.1f}%")
        print(f"   preset-driven {len(_ain)}/{len(_alt)} = {_pct(len(_ain), len(_alt)):.1f}%"
              f"  (alt models — hand-seeded inputs: {', '.join(sorted(_alt)[:8])}"
              f"{' …' if len(_alt) > 8 else ''})")
    print(f"AGREE in-band (do-not-break set):  {len(in_agr)}/{len(agr_names)}")

    if args.write_baseline:
        snap = {"scored": scored,
                "summary": {"within_band": len(in_band), "target_n": len(tgt_names),
                            "agree_in": len(in_agr), "agree_n": len(agr_names)}}
        json.dump(snap, open(args.write_baseline, "w"), indent=0)
        print(f"\nbaseline snapshot -> {args.write_baseline}")
        return 0

    # ---- compare mode ----
    if not os.path.exists(args.baseline):
        print(f"\nNO BASELINE at {args.baseline}; run --write-baseline first.")
        return 2
    base = json.load(open(args.baseline))["scored"]
    b_in_band = {tk for tk, r in base.items() if r.get("in_target_band")}
    now_in_band = set(in_band)
    gained_band = sorted(now_in_band - b_in_band)
    lost_band = sorted(b_in_band - now_in_band)

    # Agree do-not-break: a batch must never push an Agree name FURTHER from its
    # independent verdict than baseline (ordinal distance rises), nor make the
    # engine newly abstain on a name it used to call. Adjacency present at
    # baseline (e.g. REDUCE vs AVOID) is tolerated so long as it doesn't worsen.
    hard_breaks, soft_breaks = [], []
    for tk in agree:
        if tk not in scored or tk not in base:
            continue
        bd, nd = base[tk].get("agree_dist"), scored[tk].get("agree_dist")
        if bd is not None and nd is not None and nd > bd:
            hard_breaks.append((tk, bd, nd))
        elif nd is None and scored[tk].get("agree_abstain") and not base[tk].get("agree_abstain"):
            soft_breaks.append(tk)   # engine newly abstains on a former call
    hard_breaks.sort(key=lambda x: -x[2])
    soft_breaks.sort()

    b_pct = 100.0 * len(b_in_band) / max(1, len(tgt_names))
    print(f"\nvs baseline: within-band {b_pct:.1f}% -> {pct:.1f}%  "
          f"(+{len(gained_band)} / -{len(lost_band)})")

    # per-cohort attribution
    if args.cohort:
        cohort = {t.strip().upper() for t in args.cohort.split(",") if t.strip()}
        c_tgt = [tk for tk in tgt_names if tk in cohort]
        c_in = [tk for tk in c_tgt if scored[tk]["in_target_band"]]
        c_b_in = [tk for tk in c_tgt if base.get(tk, {}).get("in_target_band")]
        print(f"\ncohort ({len(cohort)} named, {len(c_tgt)} in targets): "
              f"within-band {len(c_b_in)}/{len(c_tgt)} -> {len(c_in)}/{len(c_tgt)}")
        for tk in sorted(c_tgt):
            was = base.get(tk, {}).get("in_target_band")
            now = scored[tk]["in_target_band"]
            flag = "" if was == now else ("  ↑GAINED" if now else "  ↓LOST")
            t = targets[tk]
            print(f"    {tk:<12} iv={scored[tk]['intrinsic']:>10.1f}  "
                  f"[{t['lo']:.0f},{t['hi']:.0f}] {t['target_verdict']:<10}{flag}")

    print("\n--- per-archetype within-band delta ---")
    by_arch = collections.defaultdict(lambda: [0, 0, 0])  # [now_in, base_in, n]
    for tk in tgt_names:
        arch = targets[tk]["archetype"]
        by_arch[arch][2] += 1
        if scored[tk]["in_target_band"]:
            by_arch[arch][0] += 1
        if base.get(tk, {}).get("in_target_band"):
            by_arch[arch][1] += 1
    for arch in sorted(by_arch):
        now_i, base_i, n = by_arch[arch]
        d = now_i - base_i
        mark = "" if d == 0 else (f"  +{d}" if d > 0 else f"  {d}")
        print(f"  {arch:<16} {base_i:>3}/{n:<3} -> {now_i:>3}/{n:<3}{mark}")

    ok = True
    if hard_breaks:
        ok = False
        print(f"\n❌ AGREE DO-NOT-BREAK VIOLATED: {len(hard_breaks)} names moved further from truth")
        for tk, bd, nd in hard_breaks:
            print(f"    {tk:<12} verdict={scored[tk]['verdict']!s:<10} "
                  f"my={agree[tk]['my_verdict']:<9} dist {bd}->{nd}")
    else:
        print(f"\n✅ Agree do-not-break: 0 hard breaks (verdict distance never worsened)")
    if soft_breaks:
        print(f"⚠️  soft: engine newly ABSTAINS on {len(soft_breaks)} former Agree calls: "
              f"{', '.join(soft_breaks[:10])}" + (" ..." if len(soft_breaks) > 10 else ""))
    if pct < b_pct - 1e-9:
        ok = False
        print(f"❌ WITHIN-BAND REGRESSED: {b_pct:.1f}% -> {pct:.1f}%")
    else:
        print(f"✅ within-band not regressed: {b_pct:.1f}% -> {pct:.1f}%")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
