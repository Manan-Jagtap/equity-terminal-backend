"""
app/price_hygiene.py — bad-tick filter for vendor price series.

Dhan's split/bonus adjustment skips the NSE special-session candles (muhurat
Sundays, Saturday DR drills, Budget-day sessions) for names with later
corporate actions — leaving V-shaped one-day spikes: close drops (or jumps)
>40% and reverts to the prior level the next session (ABFRL x0.37→x2.71,
TRENT x1.5 V's, found by the 500-name re-audit). No real market move does
that; sustained repricings (real demergers like TMPV Oct-2025) have no
reversion and are kept. Pure function, tested.
"""
from __future__ import annotations

SPIKE = 1.4          # >40% single-session move…
REVERT = 0.15        # …that reverts to within ±15% of the pre-spike close


def drop_bad_ticks(rows: list[dict]) -> list[dict]:
    """rows: [{'close': float, ...}] oldest→newest. Returns a new list with
    V-spike candles removed. Never drops first/last rows or sustained moves."""
    if len(rows) < 3:
        return list(rows)
    out = [rows[0]]
    i = 1
    while i < len(rows) - 1:
        prev_c = out[-1].get("close")
        cur_c = rows[i].get("close")
        next_c = rows[i + 1].get("close")
        if prev_c and cur_c and next_c:
            r_in = cur_c / prev_c
            reverts = abs(next_c / prev_c - 1.0) <= REVERT
            if (r_in > SPIKE or r_in < 1 / SPIKE) and reverts:
                i += 1          # drop the spike candle
                continue
        out.append(rows[i])
        i += 1
    out.append(rows[-1])
    return out
