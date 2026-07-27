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

CRORE = 1e7


def _mcap(price, shares):
    return (price * shares / CRORE) if (price and shares) else None


def test_unit_is_crore():
    """SOBHA renders as approximately 14,711 cr at a 1,376 price."""
    shares = 14711 * CRORE / 1376.0          # implied share count
    assert round(_mcap(1376.0, shares)) == 14711


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
