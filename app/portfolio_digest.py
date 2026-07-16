"""
app/portfolio_digest.py — a glanceable portfolio digest.

A mechanical "what your book looks like right now, and what changed since you
last looked" summary, assembled from data the terminal already publishes:
value & P&L, the biggest movers, names the model now flags (AVOID/REDUCE),
single-name concentration, earnings due this week, and the tax opportunities
surfaced by portfolio_tax. `prev_snap` (the value stored at the last digest)
drives the since-last delta.

Pure and DB-free — the route feeds it items/totals/tax and persists the new
snapshot. Educational restatement, not advice.
"""
from __future__ import annotations
import datetime as _dt


def build_digest(items: list[dict], totals: dict, tax: dict | None,
                 results_due: dict | None, prev_snap: dict | None) -> dict:
    live = [i for i in items if i.get("value")]
    today = _dt.date.today()

    headline = {
        "value": totals.get("value"),
        "cost": totals.get("cost"),
        "total_pnl": totals.get("total_pnl"),
        "total_pnl_pct": totals.get("total_pnl_pct"),
        "weighted_mos": totals.get("weighted_mos"),
        "xirr": totals.get("xirr"),
        "n": len(live),
        "as_of": today.isoformat(),
    }

    # Since-last: compare today's value against the value stored at the previous
    # digest (if any). Honest about the gap in days so a stale prior reads right.
    since = None
    if prev_snap and prev_snap.get("value") is not None and totals.get("value") is not None:
        pv = prev_snap["value"]
        since = {
            "prev_date": prev_snap.get("date"),
            "prev_value": pv,
            "delta": totals["value"] - pv,
            "delta_pct": ((totals["value"] - pv) / pv) if pv else None,
        }

    # Biggest movers by capital P&L% (need a real cost basis to be meaningful).
    ranked = sorted([i for i in live if i.get("pnl_pct") is not None],
                    key=lambda x: x["pnl_pct"])
    def mv(i):
        return {"ticker": i["ticker"], "name": i.get("name"),
                "pnl_pct": i.get("pnl_pct"), "pnl": i.get("pnl"),
                "weight": i.get("weight")}
    movers = {"gainers": [mv(i) for i in reversed(ranked) if i["pnl_pct"] > 0][:3],
              "losers":  [mv(i) for i in ranked if i["pnl_pct"] < 0][:3]}

    # Attention list — each a mechanical flag on the terminal's own published data.
    attention: list[dict] = []
    for i in sorted(live, key=lambda x: -(x.get("value") or 0)):
        verdict = "REDUCE" if i.get("verdict") == "TRIM" else i.get("verdict")
        if verdict in ("SELL", "REDUCE", "AVOID"):
            attention.append({
                "kind": "verdict", "ticker": i["ticker"], "name": i.get("name"),
                "text": f"model verdict is {verdict}"
                        + (f" (MoS {i['mos']*100:+.0f}%)" if i.get("mos") is not None else ""),
            })
    top = max(live, key=lambda x: x.get("weight") or 0, default=None)
    if top and (top.get("weight") or 0) > 0.30:
        attention.append({"kind": "concentration", "ticker": top["ticker"],
                          "name": top.get("name"),
                          "text": f"{top['weight']*100:.0f}% of the book — single-name concentration"})
    held = {i["ticker"] for i in live}
    for tk, r in sorted((results_due or {}).items(), key=lambda kv: kv[1]["days_away"]):
        if tk in held:
            d = r["days_away"]
            when = "reported" if d < 0 else ("reports today" if d == 0 else f"reports in {d}d")
            attention.append({"kind": "results", "ticker": tk, "text": f"{when} (earnings)"})

    # Tax opportunities pulled straight from the tax block.
    tax_ops = None
    if tax:
        harvest = tax.get("harvest") or []
        timing = tax.get("lt_timing") or []
        tax_ops = {
            "harvest_n": len(harvest),
            "harvest_loss": round(sum(h["loss"] for h in harvest), 2) if harvest else 0.0,
            "timing_n": len(timing),
            "timing_saving": round(sum(t["tax_saving"] for t in timing), 2) if timing else 0.0,
            "estimated_tax": (tax.get("estimated_tax") or {}).get("total"),
        }

    return {
        "headline": headline,
        "since_last": since,
        "movers": movers,
        "attention": attention[:8],
        "tax": tax_ops,
        "results_due_n": len({tk for tk in (results_due or {}) if tk in held}),
        "disclaimer": ("A mechanical snapshot of your book from the terminal's own "
                       "published figures — not investment advice."),
    }


def snapshot_from(totals: dict) -> dict:
    """The small record persisted so the next digest can show a since-last delta."""
    return {"date": _dt.date.today().isoformat(), "value": totals.get("value"),
            "total_pnl": totals.get("total_pnl")}
