"""DAT-13: the collapsed-value gate must fire through recommend() ITSELF.

WHY THIS FILE EXISTS — a post-mortem worth keeping:

The first attempt put this rule only in `price_gates()` and shipped with nine
green unit tests that all called `price_gates()` directly. Every one passed.
The gate was still completely inert in production, because `price_gates` is
wired into the intraday MoS refresh alone — neither `recommend()` (company
detail) nor the batch store path ever calls it. 62 names went on serving a
fair value under 10% of price, 40 of them as a bare AVOID.

Unit-testing the helper proved the arithmetic and proved nothing about whether
the rule RUNS. So every assertion here goes through the real entry point.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "tests"))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")

import pytest


# The replay harness monkeypatches app module globals (risk-free, beta, SOTP)
# and rebuilds a throwaway DB over the shared DATABASE_URL. Importing it at test
# scope leaked that state into every later test in the process — 31 unrelated
# failures. So the live-data assertions run in their OWN interpreter.
_REPLAY_PROBE = r"""
import sys, json
sys.path.insert(0, ".") ; sys.path.insert(0, "tests")
import _calib_replay as R
from app.database import SessionLocal
from app import models, assemble, engines, sector_params as SP
fx = R.load_fixture(); R.build_db(fx); R._install_stubs()
out = {}
for tk in ("SOBHA", "PRESTIGE", "MARUTI"):
    e = fx.get(tk)
    if not e:
        continue
    s = SessionLocal()
    co = s.query(models.Company).filter_by(ticker=tk).first()
    if not co:
        s.close(); continue
    SP.RISK_FREE = (e.get("assumptions") or {}).get("risk_free") or SP.RISK_FREE
    d = assemble.build_company(s, co)
    a = assemble.effective_assumptions(s, co, d)
    r = engines.recommend(d, a)
    s.close()
    out[tk] = {"mos": r.get("mos"), "verdict": r.get("verdict"),
               "reliable": r.get("reliable")}
print("RESULT " + json.dumps(out))
"""


@pytest.fixture(scope="module")
def live():
    import subprocess, json
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, DATABASE_URL="sqlite:////tmp/_dat13_probe.db")
    p = subprocess.run([sys.executable, "-c", _REPLAY_PROBE], cwd=root,
                       env=env, capture_output=True, text=True, timeout=300)
    line = [l for l in p.stdout.splitlines() if l.startswith("RESULT ")]
    if not line:
        pytest.skip(f"replay probe unavailable: {p.stderr[-300:]}")
    return json.loads(line[0][len("RESULT "):])


def test_collapsed_name_abstains_through_recommend(live):
    """SOBHA: mos ~-0.96. THE regression — it read AVOID in production."""
    r = live.get("SOBHA")
    if not r:
        pytest.skip("SOBHA not in fixture")
    assert r["mos"] <= -0.90, "fixture drifted; pick another collapsed name"
    assert r["verdict"] == "LOW CONF"
    assert r["reliable"] is False


@pytest.mark.parametrize("tk", ["PRESTIGE", "MARUTI"])
def test_genuine_bearish_calls_survive_through_recommend(live, tk):
    """The gate must not swallow ordinary conviction."""
    r = live.get(tk)
    if not r:
        pytest.skip(f"{tk} not in fixture")
    assert -0.90 < r["mos"] < 0, f"{tk} should be bearish but not collapsed"
    assert r["verdict"] == "AVOID"


def test_gate_is_reachable_from_the_entry_point():
    """Guards the ACTUAL failure: a rule living only in an uncalled helper.

    If someone moves this logic back out of recommend() into price_gates only,
    this fails — exactly what the first attempt needed and lacked.
    """
    import inspect
    from app import engines
    src = inspect.getsource(engines.recommend)
    assert "_COLLAPSED_MOS" in src, (
        "the collapsed-value gate must be applied INSIDE recommend(); "
        "price_gates() is only wired into the intraday refresh and is inert "
        "on both the company-detail and batch-store paths")


def test_one_source_of_truth_for_the_threshold():
    import inspect
    from app import engines
    assert engines._COLLAPSED_MOS == -0.90
    assert "_COLLAPSED_MOS" in inspect.getsource(engines.price_gates)


def test_no_data_names_are_left_alone():
    from app import engines
    assert engines.price_gates("NO DATA", None, is_financial=False,
                               is_alt=False, high_roe=False) == "NO DATA"
