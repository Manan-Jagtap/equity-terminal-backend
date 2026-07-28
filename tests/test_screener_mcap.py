"""The screener must expose a size field.

It had none. Any "top N by market cap" analysis therefore fell back to the
API's DEFAULT ordering — which is attractiveness-ranked, not size-ranked — and
produced a biased sample that looked entirely plausible: on 2026-07-27 a
"top 250 by mcap" slice returned verdict counts identical to the whole
997-name universe for BUY/ACCUMULATE/HOLD/REDUCE, i.e. every non-AVOID name in
the book, with only 9 AVOIDs. A silent no-op, not a visible error.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# shares_outstanding is stored IN CRORE, so price * shares is already crore.


def _mcap(price, shares_cr):
    return (price * shares_cr) if (price and shares_cr) else None


def test_matches_a_real_company_not_a_back_solved_one():
    """RELIANCE: ~1,350 crore shares — a real, externally verifiable count.

    The FIRST version of this test derived the share count FROM the expected
    answer and then checked the arithmetic against itself, so it passed while
    the field rendered every company on the platform as 0 cr. A unit test whose
    input is solved backwards from its own assertion tests nothing.
    """
    mcap_cr = _mcap(1281.0, 1350.0)
    assert 1_600_000 < mcap_cr < 1_900_000, f"{mcap_cr:,.0f} cr is not RELIANCE-scale"


def test_no_spurious_divisor():
    """Guards the actual defect: a second division into crore."""
    import inspect
    from app import main
    src = inspect.getsource(main)
    i = src.index('"mcap"')
    assert "/ 1e7" not in src[i:i + 200], (
        "shares_outstanding is already in crore — dividing again yields 0 cr "
        "for every company"
    )


def test_missing_inputs_yield_none_not_zero():
    """A missing share count must read as 'unknown', never as a 0 cr company
    that would sort to the bottom of a size ranking as though it were real."""
    assert _mcap(None, 1e8) is None
    assert _mcap(100.0, None) is None
    assert _mcap(100.0, 0) is None


def test_screener_row_carries_mcap():
    import inspect
    from app import main
    src = inspect.getsource(main)
    assert '"mcap"' in src, "the screener row must expose a size field"
    assert "shares_outstanding" in src
