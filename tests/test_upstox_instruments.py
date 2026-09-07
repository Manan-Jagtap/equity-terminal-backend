"""app.upstox.instruments.parse_master — two traps found porting off Dhan, both
against the LIVE master, both silent (200 OK, just the wrong or no data).

1. segment vs instrument_type (found first): REITs/InvITs are typed RR/IV and
   surveillance names BE/BZ, so filtering on instrument_type=="EQ" instead of
   segment=="NSE_EQ" silently drops 23 live names. Fixed by filtering on
   segment; see the module docstring.

2. trading_symbol collisions (found via the live cross-check dashboard, after
   the fix above shipped): a symbol is not unique WITHIN NSE_EQ. `ELECTCAST`
   also names "ELECTCAST WARRANTS" (type W1); `MOTHERSON` also names a
   Samvardhana Motherson debenture (type D1). First-wins landed on the
   warrant/bond for both — both illiquid, so LTP read 0.0 and historical
   candles came back empty. cross_check.py correctly flagged both as stale;
   the true cause was upstream in the ticker resolution, not the backfill job.
   Only 4 symbols in the whole master collide (measured against the live
   master 2026-09-07); 2 are real companies, both broken by first-wins.
"""
from app.upstox.instruments import parse_master


def _row(symbol, itype, key, segment="NSE_EQ", **extra):
    return {"segment": segment, "trading_symbol": symbol, "instrument_type": itype,
            "instrument_key": key, **extra}


def test_plain_equity_resolves():
    rows = [_row("RELIANCE", "EQ", "NSE_EQ|INE002A01018")]
    eq, _, _ = parse_master(rows)
    assert eq["RELIANCE"] == "NSE_EQ|INE002A01018"


def test_a_warrant_sharing_the_symbol_does_not_shadow_the_equity():
    """The exact live shape: warrant listed BEFORE the equity in the file."""
    rows = [
        _row("ELECTCAST", "W1", "NSE_EQ|INE086A13016"),   # ELECTCAST WARRANTS
        _row("ELECTCAST", "EQ", "NSE_EQ|INE086A01029"),   # the actual stock
    ]
    eq, _, _ = parse_master(rows)
    assert eq["ELECTCAST"] == "NSE_EQ|INE086A01029"


def test_a_debenture_sharing_the_symbol_does_not_shadow_the_equity():
    """The exact live shape: debenture listed BEFORE the equity in the file."""
    rows = [
        _row("MOTHERSON", "D1", "NSE_EQ|INE775A08105"),   # a listed debenture
        _row("MOTHERSON", "EQ", "NSE_EQ|INE775A01035"),   # the actual stock
    ]
    eq, _, _ = parse_master(rows)
    assert eq["MOTHERSON"] == "NSE_EQ|INE775A01035"


def test_equity_wins_regardless_of_file_order():
    """Order must not matter — EQ is a preference, not a first-wins default."""
    eq_first = parse_master([
        _row("X", "EQ", "NSE_EQ|EQKEY"), _row("X", "D1", "NSE_EQ|BONDKEY"),
    ])[0]
    eq_second = parse_master([
        _row("X", "D1", "NSE_EQ|BONDKEY"), _row("X", "EQ", "NSE_EQ|EQKEY"),
    ])[0]
    assert eq_first["X"] == eq_second["X"] == "NSE_EQ|EQKEY"


def test_reits_and_be_series_still_resolve_with_no_collision():
    """The FIRST trap (segment vs instrument_type) must stay fixed: a REIT
    (type RR) with no competing EQ row for its symbol must still resolve."""
    rows = [_row("EMBASSY", "RR", "NSE_EQ|INE041025011")]
    eq, _, _ = parse_master(rows)
    assert eq["EMBASSY"] == "NSE_EQ|INE041025011"


def test_no_eq_variant_falls_back_to_whatever_exists():
    """A symbol with no EQ row at all (no live example known, but the code must
    not raise or drop it) still resolves to something rather than nothing."""
    rows = [_row("Y", "N1", "NSE_EQ|SOMEKEY")]
    eq, _, _ = parse_master(rows)
    assert eq["Y"] == "NSE_EQ|SOMEKEY"
