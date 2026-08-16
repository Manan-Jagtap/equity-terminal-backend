"""Per-run test database, loaded before any test module.

35 test files each do:

    os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")

— one fixed path, shared by every run, every branch and every developer on the
machine, and never cleaned up. Two failure modes came out of that:

  * `test_api_budget::test_record_accumulates_and_reads` failed with
    "attempt to write a readonly database" for most of this session and was
    repeatedly written off as a benign pre-existing red line;
  * when that file is left locked or read-only, the damage is NOT contained —
    a run on 2026-07-27 cascaded to 16 failures + 1 collection error, and
    passed again after nothing more than `rm /tmp/_pytest_terminal.db`. A
    stale artefact was masquerading as broken code, which is exactly the kind
    of noise that hides a real regression.

pytest imports the rootdir conftest before collecting test modules, so setting
DATABASE_URL here wins over every `setdefault` above — no edits to the 35
files, and nothing to remember in a new one.
"""
import atexit
import os
import shutil
import tempfile

_TMPDIR = tempfile.mkdtemp(prefix="equity-terminal-tests-")
_DB_PATH = os.path.join(_TMPDIR, "terminal.db")

# Absolute path → sqlite needs four slashes; _DB_PATH already carries the first.
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"


@atexit.register
def _cleanup() -> None:
    """Leave no artefact behind — that is the whole point."""
    shutil.rmtree(_TMPDIR, ignore_errors=True)


# ── vendor_meter is process-global; reset it between tests ───────────────────
#
# app.vendor_meter holds since-boot counters, a last-ok/last-fail pair and a
# 20-outcome ring in MODULE globals. /api/health reads them, so ANY test that
# asserts status == "ok" is hostage to whichever earlier test last drove a
# failing vendor call — and pytest shares one process across the whole suite.
#
# This has now bitten three separate times, each presenting as "passes alone,
# fails in the suite", and each costing a bisect to re-diagnose:
#   * the first unmeasured-signals attempt, which was REVERTED over it
#   * tests/test_fix05_health_integrity.py
#   * tests/test_eod_session_coverage.py
# The per-file autouse fixtures those files carry are now belt-and-braces; this
# is the braces. Fixing it here rather than in each victim also means the next
# health test written does not have to know the trap exists.
#
# Safe by construction: every test that MEANS to observe the meter reloads it
# itself first (47 call sites do), so a reset before each test cannot take away
# state any test is relying on. It only clears leakage BETWEEN files.
import pytest as _pytest


@_pytest.fixture(autouse=True)
def _reset_vendor_meter():
    import importlib
    try:
        from app import vendor_meter
        importlib.reload(vendor_meter)
    except Exception:
        pass          # meter absent/unimportable is not this fixture's problem
    yield
