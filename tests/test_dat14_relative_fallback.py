"""DAT-14: recovery from a negative primary DCF must not depend on preset coverage.

Confirmed at app/engines.py: a negative primary zeroes `iv`, then
`alternative_intrinsic` OVERRIDES it. So of the 4 names universe-wide with a
negative primary, ADANIENT and GODREJIND published verdicts purely because
someone had written them a SOTP_PRESETS entry, while AWFIS and GODREJPROP
returned NO DATA because nobody had. The operative rule was "negative DCF AND
no preset -> abstain" — preset coverage masquerading as judgement.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/_pytest_terminal.db")

import inspect

from app import engines


def test_fallback_exists_inside_recommend():
    src = inspect.getsource(engines.recommend)
    assert "relative_only" in src, (
        "the relative fallback must live in recommend(), where the alt-model "
        "override happens — anywhere else and preset coverage still decides")


def test_fallback_requires_two_positive_legs():
    """One stray multiple must not carry a name (min-2 triangulation)."""
    src = inspect.getsource(engines.recommend)
    assert "len(_rel) >= 2" in src


def test_fallback_only_fires_when_iv_is_none():
    """It must never override a working blend or a preset."""
    src = inspect.getsource(engines.recommend)
    i = src.index("relative_only = False")
    assert "if iv is None:" in src[i:i + 300], (
        "the fallback must be conditional on iv being None, or it would "
        "displace valid primary and alt-model values")


def test_relative_only_is_forced_to_low_conf():
    """A relative-only figure is a reference, never a published conviction."""
    src = inspect.getsource(engines.recommend)
    i = src.index("if relative_only and verdict not in")
    window = src[i:i + 400]
    assert 'verdict = "LOW CONF"' in window
    assert "reliable = False" in window
    assert '"level": "low"' in window
