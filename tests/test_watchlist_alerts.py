"""
tests/test_watchlist_alerts.py — locks the valuation-alert logic.
Run: python tests/test_watchlist_alerts.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.watchlist_alerts import compute_alerts, verdict_rank

ON = {"alert_verdict": 1, "alert_mos": 1, "alert_target": 1, "alert_move": 1,
      "mos_threshold": 0.15, "move_threshold": 0.08, "target_price": 1000.0}


def types(alerts):
    return {a["type"] for a in alerts}


def test_verdict_upgrade_fires_good():
    cfg = {**ON, "last_verdict": "HOLD"}
    a = compute_alerts(cfg, {"verdict": "BUY", "mos": 0.0, "price": 1200, "day_move": 0.0})
    assert "verdict" in types(a)
    v = next(x for x in a if x["type"] == "verdict")
    assert v["level"] == "good" and "upgraded" in v["message"]
    print("  ok verdict upgrade")


def test_verdict_downgrade_warns():
    cfg = {**ON, "last_verdict": "BUY"}
    a = compute_alerts(cfg, {"verdict": "AVOID", "mos": 0.0, "price": 1200, "day_move": 0.0})
    v = next(x for x in a if x["type"] == "verdict")
    assert v["level"] == "warn" and "downgraded" in v["message"]
    print("  ok verdict downgrade")


def test_no_verdict_alert_without_prior_or_change():
    # first observation (no last_verdict) → no verdict alert
    a = compute_alerts({**ON, "last_verdict": None},
                       {"verdict": "BUY", "mos": 0.0, "price": 1200, "day_move": 0.0})
    assert "verdict" not in types(a)
    # unchanged verdict → no alert
    a2 = compute_alerts({**ON, "last_verdict": "BUY"},
                        {"verdict": "BUY", "mos": 0.0, "price": 1200, "day_move": 0.0})
    assert "verdict" not in types(a2)
    print("  ok no spurious verdict alert")


def test_mos_threshold():
    cfg = {**ON, "last_verdict": "HOLD"}
    a = compute_alerts(cfg, {"verdict": "HOLD", "mos": 0.22, "price": 1200, "day_move": 0.0})
    assert "mos" in types(a)
    # below threshold → no mos alert
    a2 = compute_alerts(cfg, {"verdict": "HOLD", "mos": 0.10, "price": 1200, "day_move": 0.0})
    assert "mos" not in types(a2)
    print("  ok mos threshold")


def test_price_target():
    cfg = {**ON, "last_verdict": "HOLD"}
    a = compute_alerts(cfg, {"verdict": "HOLD", "mos": 0.0, "price": 980, "day_move": 0.0})
    assert "target" in types(a)
    a2 = compute_alerts(cfg, {"verdict": "HOLD", "mos": 0.0, "price": 1050, "day_move": 0.0})
    assert "target" not in types(a2)
    print("  ok price target")


def test_big_move_direction():
    cfg = {**ON, "last_verdict": "HOLD"}
    drop = compute_alerts(cfg, {"verdict": "HOLD", "mos": 0.0, "price": 900, "day_move": -0.11})
    spike = compute_alerts(cfg, {"verdict": "HOLD", "mos": 0.0, "price": 900, "day_move": 0.12})
    assert next(x for x in drop if x["type"] == "move")["level"] == "good"
    assert next(x for x in spike if x["type"] == "move")["level"] == "info"
    # small move → nothing
    assert "move" not in types(compute_alerts(cfg, {"verdict": "HOLD", "mos": 0, "price": 900, "day_move": 0.02}))
    print("  ok big move")


def test_flags_off_suppress():
    cfg = {"alert_verdict": 0, "alert_mos": 0, "alert_target": 0, "alert_move": 0,
           "mos_threshold": 0.15, "move_threshold": 0.08, "target_price": 1000.0,
           "last_verdict": "HOLD"}
    a = compute_alerts(cfg, {"verdict": "BUY", "mos": 0.5, "price": 500, "day_move": -0.2})
    assert a == []
    print("  ok flags off")


def test_none_safe():
    cfg = {**ON, "last_verdict": None}
    a = compute_alerts(cfg, {"verdict": None, "mos": None, "price": None, "day_move": None})
    assert a == []
    assert verdict_rank("BUY") > verdict_rank("HOLD") > verdict_rank("AVOID")
    print("  ok none-safe")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\nAll {len(fns)} watchlist-alert tests passed.")
