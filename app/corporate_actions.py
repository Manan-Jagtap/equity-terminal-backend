"""
corporate_actions.py — pure, DB-free price back-adjustment + total-return math.

Deliberately kept OUT of engines.py. The valuation engine (app/engines.py ↔
src/lib/engine.js) is under a bit-exact parity contract; corporate-action
handling is a backend-only concern applied UPSTREAM of the engine (when the
price series is assembled) and in the returns layer (backtest / portfolio).
Nothing in this module is mirrored to JavaScript, so it is free to evolve.

An `action` is a plain dict (as produced by the ingester / read from the
CorporateAction table):

    {"action_type": "dividend" | "split" | "bonus",
     "ex_date": "YYYY-MM-DD",
     "value":   float | None,     # ₹/share, dividends only
     "ratio":   float | None}     # PRE-event price multiplier, split/bonus only

  · split/bonus `ratio` is the factor by which every close STRICTLY BEFORE the
    ex-date must be multiplied to sit on today's per-share basis.
    Split face-value 2→1 ⇒ 0.5 ; bonus 4:1 ⇒ 0.2 ; bonus 1:1 ⇒ 0.5.
  · dividend `value` is the per-share cash payout.

Back-adjustment rule: a close observed on date D is expressed on the CURRENT
per-share basis by multiplying it by the product of every split/bonus ratio
whose ex-date is strictly after D. (On/after the ex-date the close already
reflects the action, so it is not adjusted.)
"""
from __future__ import annotations


def _iso(d) -> str:
    """First 10 chars ('YYYY-MM-DD') of an ISO date string, else ''."""
    return d[:10] if isinstance(d, str) and d else ""


def price_factor(date, actions) -> float:
    """Back-adjustment multiplier for a raw close observed on `date`.

    1.0 when there are no split/bonus events after `date` (the common case),
    so callers can treat an all-dividend or empty action list as a no-op.
    """
    d = _iso(date)
    if not d:
        return 1.0
    f = 1.0
    for a in actions or ():
        if a.get("action_type") not in ("split", "bonus"):
            continue
        r = a.get("ratio")
        ex = _iso(a.get("ex_date"))
        if r and ex and ex > d:          # event strictly AFTER this close → rebase
            f *= r
    return f


def adjust_price_series(points, actions, fields=("open", "high", "low", "close")):
    """Return a NEW list of point dicts with OHLC fields back-adjusted onto the
    current per-share basis.

    Each item in `points` must carry a 'date' key ('YYYY-MM-DD'). Volume is left
    untouched (share-count adjustment is a separate concern the terminal does not
    chart). Returns a shallow copy unchanged when there are no split/bonus events,
    so the function is safe and idempotent to call on every read.
    """
    sb = [a for a in (actions or ())
          if a.get("action_type") in ("split", "bonus") and a.get("ratio")]
    if not sb:
        return list(points)
    out = []
    for p in points:
        f = price_factor(p.get("date"), sb)
        q = dict(p)
        if f != 1.0:
            for k in fields:
                if q.get(k) is not None:
                    q[k] = q[k] * f
        out.append(q)
    return out


def dividends_in_window(start_date, end_date, actions) -> float:
    """Sum of per-share cash dividends with start_date < ex_date <= end_date,
    each scaled onto the current per-share basis so a dividend paid before a
    later split/bonus is diluted consistently with the adjusted price.

    An empty `start_date`/`end_date` is treated as open-ended on that side.
    """
    s, e = _iso(start_date), _iso(end_date)
    total = 0.0
    for a in actions or ():
        if a.get("action_type") != "dividend":
            continue
        ex = _iso(a.get("ex_date"))
        v = a.get("value")
        if v and ex and (not s or ex > s) and (not e or ex <= e):
            total += v * price_factor(ex, actions)
    return total


def total_return(start_date, end_date, start_price, end_price, actions):
    """Split/bonus- and dividend-adjusted return between two dated observations.

    Returns {'price', 'dividend', 'total'} as fractions, or None when inputs are
    unusable. Both prices are put on the current per-share basis first, so a
    split INSIDE the window is handled correctly (not just at the endpoints).
    'price' reproduces the legacy price-only return when there are no actions.
    """
    if not start_price or not end_price or start_price <= 0:
        return None
    acts = list(actions or ())
    adj_start = start_price * price_factor(start_date, acts)
    adj_end = end_price * price_factor(end_date, acts)
    if adj_start <= 0:
        return None
    price_ret = adj_end / adj_start - 1.0
    div_ret = dividends_in_window(start_date, end_date, acts) / adj_start
    return {"price": price_ret, "dividend": div_ret, "total": price_ret + div_ret}
