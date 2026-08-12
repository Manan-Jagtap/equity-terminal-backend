"""The spend counter must reset when the VENDOR's counter resets.

Measured on 2026-08-12, the day after an 11th-of-month plan renewal: the vendor
console read 0 used / 10,000 remaining while our ApiUsage table read 9,081,
because our bucket ("2026-08") still carried ten days of PRE-renewal spend.

Over-counting is the safe direction. The dangerous one is the 1st of each
calendar month: a calendar bucket resets to zero while the vendor's cycle is
~20 days old and mostly spent, so the guard would permit a full month's budget
on top of a nearly exhausted plan — the exact failure the guard exists to stop.
"""
import datetime as dt
import importlib
import os

import pytest


def _budget(cycle_day):
    os.environ["INDIANAPI_CYCLE_DAY"] = str(cycle_day)
    import app.api_budget as b
    return importlib.reload(b)


@pytest.fixture(autouse=True)
def _restore():
    yield
    os.environ["INDIANAPI_CYCLE_DAY"] = "1"
    import app.api_budget as b
    importlib.reload(b)


def test_day_after_renewal_starts_a_new_bucket():
    b = _budget(11)
    assert b.current_month(dt.date(2026, 8, 12)) == "2026-08"


def test_day_before_renewal_is_still_the_previous_cycle():
    b = _budget(11)
    assert b.current_month(dt.date(2026, 8, 10)) == "2026-07"


def test_calendar_rollover_does_NOT_reset_the_cycle():
    """THE case that matters: 1 Sep is 20 days into the cycle that began 11 Aug.
    A calendar bucket would reset here and hand out a fresh budget."""
    b = _budget(11)
    assert b.current_month(dt.date(2026, 9, 1)) == "2026-08"
    assert b.current_month(dt.date(2026, 9, 10)) == "2026-08"
    assert b.current_month(dt.date(2026, 9, 11)) == "2026-09"


def test_year_boundary():
    b = _budget(11)
    assert b.current_month(dt.date(2026, 1, 5)) == "2025-12"
    assert b.current_month(dt.date(2026, 1, 11)) == "2026-01"


def test_default_is_the_old_calendar_behaviour_exactly():
    """Inert until the renewal day is configured — no silent change on deploy."""
    b = _budget(1)
    for d in (dt.date(2026, 9, 1), dt.date(2026, 8, 10), dt.date(2026, 1, 5)):
        assert b.current_month(d) == d.strftime("%Y-%m")


def test_cycle_day_is_clamped():
    assert _budget(31).cycle_day() == 28      # no 29-31 gaps in short months
    assert _budget(0).cycle_day() == 1
    os.environ["INDIANAPI_CYCLE_DAY"] = "nonsense"
    import app.api_budget as b
    assert importlib.reload(b).cycle_day() == 1
