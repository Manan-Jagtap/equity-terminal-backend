"""
app/portfolio_tax.py — position-level capital-gains / tax estimator.

Each holding is one tax lot (its buy_date + avg_cost — the portfolio model keeps
a single averaged position per name), marked to the live price. From the
UNREALISED gain/loss on every lot this computes, entirely mechanically:

  · short-term (held < 12 months) vs long-term gains & losses,
  · the estimated tax IF the whole book were sold today, under the current
    Indian listed-equity rules (STT-paid): STCG 20% (Sec 111A), LTCG 12.5% on
    the gain above the ₹1,25,000 annual exemption (Sec 112A), with the legal
    loss set-off order applied,
  · tax-loss-harvesting candidates (lots sitting on a loss that could offset
    gains), and
  · LTCG-timing candidates (short-term lots in gain that cross into long-term
    soon — waiting steps the rate 20% → 12.5%).

NOT tax advice — a transparent calculator over the terminal's own numbers. It
ignores STT/brokerage, indexation (removed for listed equity from FY24-25),
carry-forward of prior losses, and every other head of income. Verify with a CA.
"""
from __future__ import annotations

# Current Indian listed-equity rates (FY2024-25 onward, post 23-Jul-2024 Budget).
STCG_RATE = 0.20          # Sec 111A, STT-paid
LTCG_RATE = 0.125         # Sec 112A, STT-paid
LTCG_EXEMPTION = 125000.0  # ₹1.25L annual LTCG exemption
LT_TIMING_WINDOW = 120     # flag ST lots crossing to LT within this many days


def _lot_gain(i: dict) -> float | None:
    """Unrealised capital gain (₹) for one lot: value − cost. None if unpriced."""
    v, c = i.get("value"), i.get("cost")
    if v is None or c is None:
        return None
    return v - c


def tax_block(items: list[dict]) -> dict | None:
    """Position-level unrealised capital-gains + a sold-today tax estimate.

    `items` are the enriched holding rows (must carry value/cost/term/buy_date,
    i.e. post `_item`). Returns None when nothing is priced with a known term.
    """
    lots = [i for i in items
            if _lot_gain(i) is not None and i.get("term") in ("short", "long")]
    if not lots:
        return None

    st_gain = sum(g for i in lots if i.get("term") == "short" and (g := _lot_gain(i)) > 0)
    st_loss = sum(-g for i in lots if i.get("term") == "short" and (g := _lot_gain(i)) < 0)
    lt_gain = sum(g for i in lots if i.get("term") == "long" and (g := _lot_gain(i)) > 0)
    lt_loss = sum(-g for i in lots if i.get("term") == "long" and (g := _lot_gain(i)) < 0)

    # Legal set-off order for capital losses against gains:
    #   · long-term losses offset ONLY long-term gains,
    #   · short-term losses offset short-term gains first, then long-term gains.
    net_lt = lt_gain - lt_loss                       # LT loss vs LT gain
    net_st = st_gain - st_loss                       # ST loss vs ST gain first
    if net_st < 0 and net_lt > 0:                    # spare ST loss spills onto LT
        spill = min(-net_st, net_lt)
        net_lt -= spill
        net_st += spill

    taxable_st = max(0.0, net_st)
    taxable_lt = max(0.0, net_lt)
    stcg = taxable_st * STCG_RATE
    ltcg = max(0.0, taxable_lt - LTCG_EXEMPTION) * LTCG_RATE
    total_gain = st_gain - st_loss + lt_gain - lt_loss
    total_tax = stcg + ltcg

    # Tax-loss-harvesting candidates: lots sitting on an unrealised loss. Realising
    # them books a capital loss that offsets gains (ST loss is the most flexible —
    # it can shelter both ST and LT gains).
    harvest = sorted(
        [{"ticker": i["ticker"], "name": i.get("name"), "term": i["term"],
          "loss": round(-_lot_gain(i), 2), "loss_pct": i.get("pnl_pct")}
         for i in lots if _lot_gain(i) < 0],
        key=lambda x: -x["loss"])

    # LTCG-timing candidates: short-term lots currently in GAIN that turn
    # long-term soon. Selling after the flip is taxed at 12.5% not 20%.
    lt_timing = sorted(
        [{"ticker": i["ticker"], "name": i.get("name"),
          "days_to_lt": i.get("days_to_lt"),
          "gain": round(_lot_gain(i), 2),
          "tax_saving": round(_lot_gain(i) * (STCG_RATE - LTCG_RATE), 2)}
         for i in lots
         if i.get("term") == "short" and _lot_gain(i) > 0
         and i.get("days_to_lt") and 0 < i["days_to_lt"] <= LT_TIMING_WINDOW],
        key=lambda x: x["days_to_lt"])

    return {
        "short_term": {"gain": round(st_gain, 2), "loss": round(st_loss, 2),
                       "net": round(st_gain - st_loss, 2)},
        "long_term": {"gain": round(lt_gain, 2), "loss": round(lt_loss, 2),
                      "net": round(lt_gain - lt_loss, 2)},
        "total_unrealised_gain": round(total_gain, 2),
        "estimated_tax": {
            "stcg": round(stcg, 2),
            "ltcg": round(ltcg, 2),
            "total": round(total_tax, 2),
            "taxable_st": round(taxable_st, 2),
            "taxable_lt": round(taxable_lt, 2),
            "lt_exemption_used": round(min(taxable_lt, LTCG_EXEMPTION), 2),
            "effective_rate": (total_tax / total_gain) if total_gain > 0 else None,
        },
        "rates": {"stcg": STCG_RATE, "ltcg": LTCG_RATE, "ltcg_exemption": LTCG_EXEMPTION},
        "harvest": harvest[:8],
        "lt_timing": lt_timing[:8],
        "note": ("Mechanical estimate of the tax IF the whole book were sold today, "
                 "under current Indian listed-equity rules (STCG 20%, LTCG 12.5% "
                 "above ₹1.25L). One averaged lot per name; ignores STT/brokerage, "
                 "carry-forward losses and other income. Not tax advice — verify with a CA."),
    }
