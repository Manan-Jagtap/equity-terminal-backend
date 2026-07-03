"""
app/watchlist_alerts.py — pure valuation-alert logic (no DB), so it's unit-testable.

The route layer loads each watched name's current valuation + price and calls
`compute_alerts(cfg, cur)`; this module decides which of the four alert types fire:

  · verdict      — the engine's call crossed into a better (or worse) band
  · mos          — margin of safety reached the user's threshold
  · target       — price fell into the user's entry band / below target
  · move         — a large 1-day price move (dislocation)

Levels: "good" (actionable opportunity), "info" (notable), "warn" (risk).
"""
from __future__ import annotations

# Higher = more bullish. LOW CONF / NO DATA sit mid/low so a move OUT of them
# into BUY/ACCUMULATE still reads as an upgrade.
VERDICT_RANK = {
    "AVOID": 0, "REDUCE": 1, "NO DATA": 1, "HOLD": 2, "LOW CONF": 2,
    "ACCUMULATE": 3, "BUY": 4,
}


def verdict_rank(v) -> int:
    return VERDICT_RANK.get((v or "").upper(), 2)


def _num(x):
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def compute_alerts(cfg: dict, cur: dict) -> list[dict]:
    """cfg  = per-name config: alert_verdict/mos/target/move (bool-ish),
              mos_threshold, move_threshold, target_price, last_verdict.
       cur  = live snapshot: verdict, mos (fraction), price, day_move (fraction)."""
    cfg = cfg or {}
    cur = cur or {}
    out = []
    verdict = cur.get("verdict")
    last = cfg.get("last_verdict")
    mos = _num(cur.get("mos"))
    price = _num(cur.get("price"))
    day_move = _num(cur.get("day_move"))

    # ── Verdict transition (upgrade is the headline; downgrade surfaced as a risk).
    if cfg.get("alert_verdict") and verdict and last:
        if verdict_rank(verdict) > verdict_rank(last):
            out.append({"type": "verdict",
                        "level": "good" if verdict_rank(verdict) >= 3 else "info",
                        "message": f"Verdict upgraded {last} → {verdict}"})
        elif verdict_rank(verdict) < verdict_rank(last):
            out.append({"type": "verdict", "level": "warn",
                        "message": f"Verdict downgraded {last} → {verdict}"})

    # ── Margin-of-safety threshold reached.
    thr = _num(cfg.get("mos_threshold"))
    if cfg.get("alert_mos") and mos is not None and thr is not None and mos >= thr:
        out.append({"type": "mos", "level": "good",
                    "message": f"Margin of safety {mos*100:.0f}% ≥ {thr*100:.0f}% target"})

    # ── Price reached the entry band / target.
    tgt = _num(cfg.get("target_price"))
    if cfg.get("alert_target") and tgt and price is not None and price <= tgt:
        out.append({"type": "target", "level": "good",
                    "message": f"Price ₹{price:,.0f} at/below entry target ₹{tgt:,.0f}"})

    # ── Large 1-day move (a drop reads as an opportunity, a spike as info).
    mthr = _num(cfg.get("move_threshold"))
    if cfg.get("alert_move") and day_move is not None and mthr is not None and abs(day_move) >= mthr:
        out.append({"type": "move",
                    "level": "good" if day_move < 0 else "info",
                    "message": f"Moved {day_move*100:+.1f}% in a day — large dislocation"})

    # ── Model-signal alerts (always on — no per-name config, so no schema change).
    # Multi-factor Alpha Score entered the top decile of the universe.
    alpha = _num(cur.get("alpha_score"))
    if cur.get("alpha_top") and alpha is not None:
        out.append({"type": "alpha", "level": "good",
                    "message": f"Top Alpha decile — multi-factor score {alpha:.0f}"})

    # Consensus estimate revised UP since tracking began (a positive catalyst).
    rev = _num(cur.get("revision"))
    if rev is not None and rev >= 0.02:
        out.append({"type": "revision", "level": "good",
                    "message": f"Consensus upside revised +{rev*100:.0f} pts — estimate upgrade"})

    return out
