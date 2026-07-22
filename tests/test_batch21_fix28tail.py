"""Batch 21 — FIX-28 deferred tail (part 1): DATA-05 fiscal-year labelling +
ENG-19/DATA-08 minor hygiene (verified via a clean import + the suite)."""
import datetime as dt
from app.ingest.indianapi_ingester import _fy_label


def test_fy_label_uses_fiscal_year_not_calendar():
    # India FY ends 31 Mar; the latest COMPLETED FY is the current calendar year
    # only from April onward. Jan–Mar must fall back to the PRIOR FY.
    assert _fy_label(dt.date(2026, 2, 15)) == 2025    # Feb — the old code stamped 2026 (phantom)
    assert _fy_label(dt.date(2026, 3, 31)) == 2025    # 31 Mar — still prior FY
    assert _fy_label(dt.date(2026, 4, 1)) == 2026     # 1 Apr — new FY begins
    assert _fy_label(dt.date(2025, 6, 1)) == 2025     # mid-year — current
    assert _fy_label(dt.date(2025, 12, 31)) == 2025   # Dec — current
