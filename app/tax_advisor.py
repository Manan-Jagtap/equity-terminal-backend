"""
app/tax_advisor.py — tax-aware portfolio intelligence for the Fund Manager.

Indian listed-equity capital-gains rules (post-Jul-2024 regime) encoded once:

  · STCG (held < 12 months)  : 20%
  · LTCG (held ≥ 12 months)  : 12.5%, with a ₹1.25 lakh annual exemption
  · Set-off: STCL offsets STCG and LTCG; LTCL offsets only LTCG.
    Unused capital losses carry forward 8 assessment years.

From the enriched holdings (value / cost / pnl / term / days_to_lt) this builds
the moves a tax-smart PM actually makes:

  1. LTCG-exemption harvesting — book long-term GAINS up to the unused ₹1.25L
     free limit and re-buy: the gain is realised tax-free and the cost basis
     steps up, shrinking future tax. Pure upside if the thesis still holds.
  2. Tax-loss harvesting — book LOSSES to offset realised/again-to-be-realised
     gains; STCL is the most valuable (offsets 20%-taxed gains first).
  3. STCG→LTCG deferral — short-term WINNERS close to the 1-year mark: waiting
     drops the rate from 20% to 12.5%.
  4. After-tax proceeds on any trim the manager proposes, so "sell ₹X" is
     shown net of the tax it triggers.

Every number is mechanical and explained; NOT tax advice — the rates assume
Indian listed equity and the owner's slab doesn't change the flat CG rates,
but surcharge/cess and carry-forward specifics should be checked with a
qualified adviser. Pure functions — unit-testable without a DB.
"""
from __future__ import annotations

STCG_RATE = 0.20
LTCG_RATE = 0.125
LTCG_EXEMPTION = 125_000.0
LT_DAYS = 365


def _gain(i):
    """Unrealised capital gain (ex-dividends) on a live, dated holding."""
    return i.get("pnl") if (i.get("value") is not None and i.get("term")) else None


def estimate_tax(gain: float, term: str, lt_exemption_left: float = 0.0) -> float:
    """Tax on realising `gain` (₹). Long-term gains draw down the exemption
    first. Losses return a negative number (the tax they can save elsewhere)."""
    if gain is None:
        return 0.0
    if gain <= 0:
        # a booked loss saves tax at the rate of the gains it offsets; value it
        # conservatively at the LTCG rate (the guaranteed floor).
        return round(gain * LTCG_RATE, 0)
    if term == "long":
        taxable = max(0.0, gain - max(0.0, lt_exemption_left))
        return round(taxable * LTCG_RATE, 0)
    return round(gain * STCG_RATE, 0)


def tax_plan(items: list[dict]) -> dict:
    """Full tax read of the book + the optimisation moves. `items` are the
    enriched holdings from compute_totals (value/cost/pnl/term/days_to_lt)."""
    live = [i for i in items if i.get("value") is not None and i.get("term")]

    # Unrealised position of the book, split by term.
    lt_gain = sum(_gain(i) for i in live if i["term"] == "long" and (_gain(i) or 0) > 0)
    lt_loss = sum(_gain(i) for i in live if i["term"] == "long" and (_gain(i) or 0) < 0)
    st_gain = sum(_gain(i) for i in live if i["term"] == "short" and (_gain(i) or 0) > 0)
    st_loss = sum(_gain(i) for i in live if i["term"] == "short" and (_gain(i) or 0) < 0)

    harvest, losses, deferrals = [], [], []

    # 1) LTCG-exemption harvesting — long-term winners, cheapest gains first so
    # the free ₹1.25L stretches over as many names as possible.
    exemption_left = LTCG_EXEMPTION
    lt_winners = sorted(
        [i for i in live if i["term"] == "long" and (_gain(i) or 0) > 0],
        key=lambda i: _gain(i))
    for i in lt_winners:
        if exemption_left <= 1000:
            break
        g = _gain(i)
        book = min(g, exemption_left)
        # fraction of the position to sell to realise `book` of gain
        frac = book / g if g else 0
        harvest.append({
            "ticker": i["ticker"], "name": i.get("name"),
            "unrealised_gain": round(g),
            "harvest_gain": round(book), "sell_fraction": round(frac, 3),
            "sell_value": round((i["value"] or 0) * frac),
            "tax_saved_vs_later": round(book * LTCG_RATE)})
        exemption_left -= book

    # 2) Tax-loss harvesting — losers ranked by the tax they can shelter.
    for i in sorted([i for i in live if (_gain(i) or 0) < 0], key=lambda i: _gain(i)):
        g = _gain(i)
        losses.append({
            "ticker": i["ticker"], "name": i.get("name"), "term": i["term"],
            "unrealised_loss": round(g),
            "offsets": ("STCG (20%) then LTCG" if i["term"] == "short" else "LTCG (12.5%) only"),
            "tax_shelter": round(abs(g) * (STCG_RATE if i["term"] == "short" else LTCG_RATE))})

    # 3) STCG→LTCG deferral — short-term winners within 45 days of long-term.
    for i in live:
        if i["term"] == "short" and (_gain(i) or 0) > 0 and (i.get("days_to_lt") or 999) <= 45:
            g = _gain(i)
            deferrals.append({
                "ticker": i["ticker"], "name": i.get("name"),
                "days_to_lt": i["days_to_lt"], "unrealised_gain": round(g),
                "tax_now_stcg": round(g * STCG_RATE),
                "tax_if_deferred_ltcg": round(estimate_tax(g, "long", LTCG_EXEMPTION)),
                "saving": round(g * STCG_RATE - g * LTCG_RATE)})

    exemption_used = LTCG_EXEMPTION - exemption_left
    return {
        "regime": {"stcg_rate": STCG_RATE, "ltcg_rate": LTCG_RATE,
                   "ltcg_exemption": LTCG_EXEMPTION},
        "unrealised": {"lt_gain": round(lt_gain), "lt_loss": round(lt_loss),
                       "st_gain": round(st_gain), "st_loss": round(st_loss),
                       "net": round(lt_gain + lt_loss + st_gain + st_loss)},
        "ltcg_harvest": harvest[:6],
        "ltcg_exemption_usable": round(exemption_used),
        "loss_harvest": losses[:6],
        "st_to_lt_deferrals": sorted(deferrals, key=lambda x: x["days_to_lt"])[:6],
        "disclaimer": ("Mechanical estimates on Indian listed-equity CG rules "
                       "(STCG 20%, LTCG 12.5% over a ₹1.25L annual exemption). "
                       "Surcharge/cess and loss carry-forward specifics vary — "
                       "confirm with a tax adviser. Not tax advice."),
    }


def tax_note(plan: dict) -> str:
    """One PM-note paragraph of the tax opportunities, strictly from the plan."""
    bits = []
    h = plan.get("ltcg_harvest") or []
    use = plan.get("ltcg_exemption_usable") or 0
    if h and use > 5000:
        bits.append(f"₹{use:,.0f} of long-term gains can be booked inside this year's "
                    f"₹1.25L LTCG exemption tax-free (harvest-and-rebuy on {len(h)} "
                    f"name{'s' if len(h) != 1 else ''} resets the cost basis higher)")
    lh = plan.get("loss_harvest") or []
    if lh:
        shelter = sum(x["tax_shelter"] for x in lh)
        bits.append(f"₹{shelter:,.0f} of tax could be sheltered by booking losses on "
                    f"{len(lh)} underwater name{'s' if len(lh) != 1 else ''} to offset gains")
    df = plan.get("st_to_lt_deferrals") or []
    if df:
        save = sum(x["saving"] for x in df)
        bits.append(f"{len(df)} short-term winner{'s' if len(df) != 1 else ''} turn"
                    f"{'' if len(df) != 1 else 's'} long-term within 45 days — waiting drops "
                    f"the rate 20%→12.5% (≈₹{save:,.0f} saved)")
    if not bits:
        return ""
    return "Tax angle: " + "; ".join(bits) + "."
