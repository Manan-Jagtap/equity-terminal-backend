"""Every field valuate() guards on must be PRESENT in each captured entry.

The bug this prevents (2026-07-28): three ground-truth rows were added with a
capture field list assembled by reading an existing fixture entry's keys and
assuming completeness. It dropped `revenue` and `net_debt` — precisely the two
fields valuate() refuses to run a DCF without. All three names assembled as
None, abstained, and never scored. The rows committed cleanly, the suite was
green, and the calibration number did not move: write-only work.

The guard list is PARSED FROM engines.py rather than hardcoded, so adding a new
guard there makes this test fail until the capture is updated to match.

Presence, not non-null: a name can legitimately have `revenue: null` (vendor
gap) and honestly abstain. A MISSING KEY is a capture defect.
"""
import gzip
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "tests", "calibration_inputs.json.gz")


def guarded_fields():
    """Guarded field names per branch of valuate(), parsed from engines.py.

    The guards are BRANCH-SPECIFIC, not universal — financials take the
    Residual Income path (equity + shares) and legitimately never carry
    revenue/net_debt; 64 of 517 entries are in that position. An earlier
    version of this test required every field of every entry and failed on all
    64, which would have been "fixed" by capturing fields the engine never
    reads for those names.

    Scanned per LINE: a nested-paren regex over `_has(...)` silently matched
    nothing (it stopped at the ")" inside co.get("equity")), leaving the
    completeness check vacuous. test_guard_list_is_discoverable exists because
    that is invisible otherwise.
    """
    src = open(os.path.join(ROOT, "app", "engines.py")).read()
    fin, nonfin = set(), set()
    in_fin = False
    for line in src.splitlines():
        if 'co["type"] == "financial"' in line:
            in_fin = True
        if "_has(" in line and "co.get(" in line:
            got = set(re.findall(r'co\.get\("(\w+)"\)', line))
            (fin if in_fin else nonfin).update(got)
            in_fin = False          # the financial guard is the first one only
    return {"financial": fin, "nonfinancial": nonfin}


def required_for(entry):
    co = entry.get("company") or {}
    kind = "financial" if co.get("type") == "financial" else "nonfinancial"
    return guarded_fields()[kind]


def test_guard_list_is_discoverable():
    """If this fails the regex broke, and the test below is silently vacuous."""
    g = guarded_fields()
    assert {"equity", "shares"} <= g["financial"], g
    assert {"revenue", "net_debt", "shares"} <= g["nonfinancial"], g


def test_every_entry_carries_the_fields_its_branch_guards_on():
    with gzip.open(FIXTURE, "rt") as fh:
        fx = json.load(fh)
    assert len(fx) > 500, f"fixture unexpectedly small: {len(fx)}"
    missing = {}
    for tk, e in fx.items():
        gap = sorted(required_for(e) - set((e.get("company") or {}).keys()))
        if gap:
            missing[tk] = gap
    assert not missing, (
        "captured entries are missing fields valuate() guards on for their "
        "branch — these names will abstain and never score, silently:\n  "
        + "\n  ".join(f"{tk}: {ks}" for tk, ks in sorted(missing.items())[:15])
    )


def test_the_three_names_that_hit_this_bug_are_covered():
    """Regression pin for the specific defect."""
    with gzip.open(FIXTURE, "rt") as fh:
        fx = json.load(fh)
    for tk in ("RELIANCE", "BHARTIARTL", "INFY"):
        co = (fx.get(tk) or {}).get("company") or {}
        assert co, f"{tk} absent from fixture"
        for f in ("revenue", "net_debt", "shares"):
            assert f in co, f"{tk} missing {f} — the 2026-07-28 capture defect"
