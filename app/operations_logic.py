"""
app/operations_logic.py — DB-free extraction of operating-efficiency metrics from
the stored Screener-style `ratios` series, so it's unit-testable.

Pulls the latest value (and a 3-year-ago value, for trend) for ROCE, ROE, debtor
/ inventory / payable days, the cash-conversion cycle and working-capital days.
Sector-agnostic: metric names are matched loosely since IndianAPI's ratio labels
vary by template.
"""
from __future__ import annotations
import re


def _num(x):
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _yearkey(p):
    s = str(p)
    four = re.findall(r"\d{4}", s)
    if four:
        return int(four[-1])
    two = re.findall(r"\d{2}", s)
    if two:
        return 2000 + int(two[-1])
    return 0


def _latest_two(series):
    """Return (latest_value, value_~3y_ago) from a {period: value} dict or a list."""
    if isinstance(series, list):
        vals = [_num(x) for x in series if _num(x) is not None]
        return (vals[-1] if vals else None, vals[-4] if len(vals) >= 4 else None)
    if not isinstance(series, dict):
        return (None, None)
    items = [(p, _num(v)) for p, v in series.items() if _num(v) is not None]
    items.sort(key=lambda kv: _yearkey(kv[0]))
    if not items:
        return (None, None)
    latest = items[-1][1]
    prior = items[-4][1] if len(items) >= 4 else None
    return (latest, prior)


def operational_snapshot(ratios: dict | None) -> dict | None:
    if not isinstance(ratios, dict) or not ratios:
        return None

    def find(names):
        for n in names:
            for m, series in ratios.items():
                if n in m.lower():
                    return series
        return None

    def two(names):
        return _latest_two(find(names) or {})

    roce, roce_p = two(["roce"])
    roe, _       = two(["roe"])
    dd, _        = two(["debtor day", "receivable day", "debtor", "receivable"])
    inv, _       = two(["inventory day", "inventory turnover", "inventory"])
    pay, _       = two(["payable day", "days payable", "payable"])
    ccc, ccc_p   = two(["cash conversion", "cash conv"])
    wc, _        = two(["working capital day", "working capital"])
    atr, _       = two(["asset turnover", "fixed asset turnover", "total asset turnover"])

    if all(v is None for v in (roce, roe, dd, inv, pay, ccc, wc, atr)):
        return None

    return {
        "roce": roce,
        "roce_delta_3y": (roce - roce_p) if (roce is not None and roce_p is not None) else None,
        "roe": roe,
        "debtor_days": dd, "inventory_days": inv, "payable_days": pay,
        "ccc": ccc,
        "ccc_delta_3y": (ccc - ccc_p) if (ccc is not None and ccc_p is not None) else None,
        "wc_days": wc, "asset_turnover": atr,
    }
