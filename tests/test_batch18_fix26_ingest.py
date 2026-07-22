"""Batch 18 — FIX-26 (TEST-03): golden-fixture regression tests for the ingest
path — the highest-incident-density code, previously with zero tests. These lock
the exact prior prod bugs: misfiled statement columns (DATA-02/FIX-04) and
UTC-shifted Dhan candle dates. Mutate a parser line → a test fails."""
import datetime as dt
from app.ingest.pl_parser import parse_pl, validate_pl
from app.dhan.client import rows_from_candles, IST


# ── PL statement parser: pick the AUDITED full-year column, not a quarter ─────

# NBFC filing shape: (Rs in crore), 4 columns = 2 quarters + 2 years, with an
# audited/unaudited flag row that selects the current full-year column.
NBFC_PL = """(Rs in crore)
Particulars                       31.12.2024  30.09.2024  31.03.2025  31.03.2024
                                  Unaudited   Unaudited   Audited     Audited
Interest income                   1200.0      1150.0      4800.0      4200.0
Finance costs                     500.0       480.0       1900.0      1700.0
Profit before tax                 300.0       290.0       1200.0      1000.0
Total tax expense                 75.0        72.0        300.0       250.0
Profit after tax                  225.0       218.0       900.0       750.0
"""


def test_parse_pl_selects_audited_year_column():
    out = parse_pl(NBFC_PL, "NBFC")
    assert out["fiscal_year"] == 2025
    assert out["ncols"] == 4 and out["target_col"] == 2      # the audited FY column
    pl = out["pl"]
    assert pl["interest_income"] == 4800.0                    # year value, NOT a quarter
    assert pl["pat"] == 900.0
    assert pl["nii"] == round(4800.0 - 1900.0, 2)             # derived NII
    ok, _ = validate_pl(pl)
    assert ok


def test_parse_pl_column_drift_is_trailing_aligned():
    # DATA-02/FIX-04 class: a stray leading number (a note reference) on a line
    # gives MORE numbers than columns. The parser must trailing-align to the
    # audited-year value, not misfile the note as PAT.
    drift = NBFC_PL.replace(
        "Profit after tax                  225.0       218.0       900.0       750.0",
        "Profit after tax   7              225.0       218.0       900.0       750.0")
    out = parse_pl(drift, "NBFC")
    assert out["pl"]["pat"] == 900.0                          # not 7, not a quarter


def test_parse_pl_malformed_payload_degrades_not_crashes():
    out = parse_pl("garbled text, no parseable header at all", "NBFC")
    assert out.get("error") and out["pl"] == {}


# ── Dhan candle → IST date (the exact UTC-shift prod bug) ─────────────────────

def test_dhan_candle_date_is_ist_not_utc():
    # Midnight IST 2026-01-15 == 18:30 UTC on 2026-01-14. A UTC conversion shifted
    # every such candle back a day (Sunday-dated candles, duplicate-key crashes).
    ts = int(dt.datetime(2026, 1, 15, 0, 0, tzinfo=IST).timestamp())
    rows = rows_from_candles({"timestamp": [ts], "open": [1.0], "high": [2.0],
                              "low": [0.5], "close": [1.5], "volume": [100]})
    assert len(rows) == 1
    assert rows[0]["date"] == "2026-01-15"                    # IST, not 2026-01-14
    assert rows[0]["close"] == 1.5


def test_dhan_candles_garbled_payload_no_crash():
    assert rows_from_candles(None) == []
    assert rows_from_candles({}) == []
    assert rows_from_candles({"timestamp": ["notanumber"], "close": [1.0]}) == []
