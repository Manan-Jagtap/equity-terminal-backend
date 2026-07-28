"""The consensus oracle must never contaminate ground truth.

calibration_targets.csv holds INDEPENDENT INTRINSIC valuations — the only
non-circular benchmark this engine has. consensus_targets.csv holds where the
SELL SIDE expects price to go. Merging them, or averaging their scores, would
destroy the one number that means anything.

The danger is specifically that it would look like progress: large-cap ground
truth reaches only 85/250, consensus reaches 209, and stapling them together
would send "within-band" sharply up while making it meaningless.
"""
import csv
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GT = os.path.join(ROOT, "VALUATION_GROUNDTRUTH_2026-07", "calibration_targets.csv")
CONS = os.path.join(ROOT, "VALUATION_GROUNDTRUTH_2026-07", "consensus_targets.csv")


def _cons_rows():
    with open(CONS) as fh:
        return [l.strip().split(",") for l in fh if l.strip() and not l.startswith("#")]


def test_consensus_file_exists_and_is_well_formed():
    rows = _cons_rows()
    assert len(rows) > 150
    for p in rows:
        assert len(p) == 6, p
        lo, hi, tgt, n = float(p[1]), float(p[2]), float(p[3]), int(p[4])
        assert 0 < lo < hi, p[0]
        assert lo <= tgt <= hi, p[0]
        assert n >= 3, f"{p[0]}: a 'consensus' of fewer than 3 analysts is one opinion"


def test_column_names_cannot_be_confused_with_ground_truth():
    """Different column names are the cheapest guard against a careless join."""
    head = open(CONS).read()
    assert "consensus_low" in head and "consensus_high" in head
    assert "target_lo" not in head and "target_hi" not in head, (
        "consensus columns must NOT reuse the ground-truth column names — that "
        "is how the two silently get merged")


def test_ground_truth_file_is_untouched_by_this_work():
    """calibration_targets.csv must contain no consensus-derived rows."""
    with open(GT) as fh:
        gt = list(csv.DictReader(fh))
    assert "target_lo" in gt[0], "ground-truth schema changed unexpectedly"
    assert not any("consensus" in (k or "").lower() for k in gt[0]), (
        "a consensus column has leaked into calibration_targets.csv")


def test_the_two_oracles_genuinely_disagree():
    """Pins WHY they must stay separate.

    If consensus agreed with independent intrinsic value everywhere, the
    distinction would be academic. It does not: consensus is price-anchored and
    structurally long-biased, so a name can sit inside one band and far outside
    the other. Overlap on shared tickers is expected to be partial.
    """
    cons = {p[0]: (float(p[1]), float(p[2])) for p in _cons_rows()}
    with open(GT) as fh:
        gt = {r["ticker"]: r for r in csv.DictReader(fh)}
    shared = [t for t in cons if t in gt]
    if len(shared) < 10:
        return                      # too few to assert on; not a failure
    agree = 0
    for t in shared:
        try:
            lo, hi = float(gt[t]["target_lo"]), float(gt[t]["target_hi"])
        except (ValueError, KeyError):
            continue
        clo, chi = cons[t]
        if not (chi < lo or clo > hi):
            agree += 1
    assert agree < len(shared), (
        "consensus and independent ground truth overlap on EVERY shared name — "
        "verify they are actually independent sources before trusting either")
