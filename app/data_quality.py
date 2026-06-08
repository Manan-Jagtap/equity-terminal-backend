"""
app/data_quality.py — backend trust layer (mirror of the frontend dataQuality.js).

Returns a confidence score (0–1), level (high/medium/low) and human-readable
flags for a company dict. The recommendation engine multiplies the composite by
this confidence and refuses to issue a strong verdict on low-confidence data, so
the API never returns a confident BUY built on missing or mismatched inputs.

It also catches the most dangerous failure mode for this product: a live
(split/bonus-adjusted) price paired with stale per-share fundamentals, which
fabricates an "undervalued" signal (the Bajaj Finance P/E 3.4x / P/B 0.73x case).
"""
from __future__ import annotations


def data_quality(co: dict) -> dict:
    flags: list[str] = []
    score = 1.0
    fin = co.get("type") == "financial"

    def penal(amount: float, msg: str):
        nonlocal score
        score -= amount
        flags.append(msg)

    equity = co.get("equity")
    shares = co.get("shares")
    pat    = co.get("net_profit")
    price  = co.get("price")

    if not (equity and equity > 0):       penal(0.35, "Book equity missing or non-positive")
    if pat is None:                        penal(0.25, "Net profit (PAT) not available")
    if not (shares and shares > 0):        penal(0.20, "Shares outstanding missing")
    if not fin and co.get("revenue") is None:  penal(0.15, "Revenue not available")
    if not fin and co.get("net_debt") is None: penal(0.10, "Net debt not available")
    if co.get("synthetic_series"):         penal(0.10, "Price history is synthetic — momentum/52W not from real OHLC")
    # A missing live price makes margin-of-safety meaningless — drop confidence
    # below the reliability threshold so the verdict reads LOW CONF, never a BUY.
    if co.get("synthetic_price"):          penal(0.60, "Live price unavailable — margin of safety vs price is not meaningful")

    pb = pe = roe = None
    if equity and equity > 0 and shares and shares > 0 and price:
        pb = price / (equity / shares)
    if pat and pat > 0 and shares and shares > 0 and price:
        pe = price / (pat / shares)
    if pat is not None and equity and equity > 0:
        roe = pat / equity

    if pb is not None and pb < 0.3:
        penal(0.45, f"Implausibly low P/B ({pb:.2f}x) — price and book value look to be on different bases (possible split/stale data)")
    elif pb is not None and pb > 45:
        penal(0.15, f"Very high P/B ({pb:.1f}x) — verify book value")
    if pe is not None and pe < 3:
        penal(0.45, f"Implausibly low P/E ({pe:.1f}x) — earnings and price look mismatched (possible split/stale data)")

    # Composite trap: high ROE + very low P/E + very low P/B never co-occur for a
    # real business — it is the signature of stale/pre-split per-share data.
    if roe is not None and roe > 0.18 and pb is not None and pb < 1.6 and pe is not None and pe < 8:
        penal(0.50, f"High ROE ({roe*100:.0f}%) with very low P/E ({pe:.1f}x) and P/B ({pb:.2f}x) — almost certainly a stale/split data mismatch")

    if pat is not None and pat < 0:
        penal(0.30, "Loss-making — earnings multiples and DCF are unreliable")
    if equity is not None and equity < 0:
        penal(0.45, "Negative net worth — book/return-based valuation is not meaningful")
    if co.get("data_warning"):
        penal(0.45, co["data_warning"])

    score = max(0.0, min(1.0, score))
    level = "high" if score >= 0.8 else "medium" if score >= 0.5 else "low"
    return {"score": score, "level": level, "flags": flags}
