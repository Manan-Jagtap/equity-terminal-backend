"""
Valuation, fundamentals, technical and recommendation engines.
Fixed: handles None values for NBFC metrics on bulk-ingested companies.
"""
from typing import Dict, List


def safe(val, default=0.0):
    """Return val if not None, else default."""
    return val if val is not None else default


def cost_of_equity(a: Dict) -> float:
    return a["risk_free"] + a["beta"] * a["erp"]


def residual_income(co: Dict, a: Dict) -> Dict:
    ke = cost_of_equity(a)
    bvps0 = co["equity"] / co["shares"]
    retention = 1 - a["payout"]
    N = max(3, round(a["fade_years"]))
    bv, pv, rows = bvps0, 0.0, []
    for t in range(1, N + 1):
        roe = a["forecast_roe"] + (a["terminal_roe"] - a["forecast_roe"]) * (t / N)
        ri = (roe - ke) * bv
        disc = (1 + ke) ** t
        pv += ri / disc
        rows.append({"t": t, "roe": roe, "bv_begin": bv, "ri": ri, "pv": ri / disc})
        bv = bv * (1 + roe * retention)
    ri_next = (a["terminal_roe"] - ke) * bv
    tv = ri_next / (ke - a["terminal_growth"]) if a["terminal_growth"] < ke else 0.0
    tv_pv = tv / ((1 + ke) ** N)
    intrinsic = bvps0 + pv + tv_pv
    return {"ke": ke, "wacc": None, "bvps0": bvps0, "intrinsic": intrinsic,
            "ev": None, "pv_explicit": pv, "tv_pv": tv_pv, "rows": rows,
            "method": "Residual Income"}


def fcff_dcf(co: Dict, a: Dict) -> Dict:
    ke = cost_of_equity(a)
    ew = 1 - a["debt_weight"]
    wacc = ew * ke + a["debt_weight"] * a["cost_debt"] * (1 - a["tax_rate"])
    N = max(3, round(a["fade_years"]))
    rev, pv, rows = co["revenue"], 0.0, []
    for t in range(1, N + 1):
        g = a["rev_growth"] + (a["terminal_growth"] - a["rev_growth"]) * (t / N)
        rev = rev * (1 + g)
        ebit = rev * a["ebit_margin"]
        nopat = ebit * (1 - a["tax_rate"])
        fcff = nopat * (1 - a["reinvest_rate"])
        disc = (1 + wacc) ** t
        pv += fcff / disc
        rows.append({"t": t, "rev": rev, "fcff": fcff, "pv": fcff / disc})
    fcff_next = rows[N - 1]["fcff"] * (1 + a["terminal_growth"])
    tv = fcff_next / (wacc - a["terminal_growth"]) if a["terminal_growth"] < wacc else 0.0
    tv_pv = tv / ((1 + wacc) ** N)
    ev = pv + tv_pv
    equity_val = ev - co["net_debt"]
    intrinsic = equity_val / co["shares"]
    return {"ke": ke, "wacc": wacc, "bvps0": None, "intrinsic": intrinsic,
            "ev": ev, "equity_val": equity_val, "pv_explicit": pv, "tv_pv": tv_pv,
            "rows": rows, "method": "FCFF DCF"}


def valuate(co: Dict, a: Dict) -> Dict:
    return residual_income(co, a) if co["type"] == "financial" else fcff_dcf(co, a)


def sensitivity(co: Dict, a: Dict) -> Dict:
    rate_deltas = [-0.01, -0.005, 0, 0.005, 0.01]
    g_deltas = [-0.01, -0.005, 0, 0.005, 0.01]
    grid = [[valuate(co, {**a, "terminal_growth": a["terminal_growth"] + gd,
                          "risk_free": a["risk_free"] + rd})["intrinsic"]
             for gd in g_deltas] for rd in rate_deltas]
    return {"rate_deltas": rate_deltas, "g_deltas": g_deltas, "grid": grid}


def fundamentals(co: Dict) -> Dict:
    bvps = co["equity"] / co["shares"]
    eps = co["net_profit"] / co["shares"] if co.get("net_profit") else None
    pb = co["price"] / bvps
    pe = co["price"] / eps if eps else None
    roe = co["net_profit"] / co["equity"] if co.get("net_profit") else None
    return {"bvps": bvps, "eps": eps, "pb": pb, "pe": pe, "roe": roe}


def _sma(series: List[float], n: int):
    out = []
    for i in range(len(series)):
        if i < n - 1:
            out.append(None)
        else:
            out.append(round(sum(series[i - n + 1:i + 1]) / n, 1))
    return out


def _rsi(series: List[float], n: int = 14) -> float:
    gains = losses = 0.0
    for i in range(1, n + 1):
        ch = series[i] - series[i - 1]
        gains += max(ch, 0); losses += max(-ch, 0)
    ag, al = gains / n, losses / n
    for i in range(n + 1, len(series)):
        ch = series[i] - series[i - 1]
        ag = (ag * (n - 1) + max(ch, 0)) / n
        al = (al * (n - 1) + max(-ch, 0)) / n
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


def technicals(co: Dict) -> Dict:
    closes = [p["close"] for p in co["series"]]
    sma20 = _sma(closes, 20)
    sma50 = _sma(closes, 50)
    data = [{"i": p["i"], "close": p["close"], "sma20": sma20[k], "sma50": sma50[k]}
            for k, p in enumerate(co["series"])]
    last = closes[-1]
    above50 = sma50[-1] is not None and last > sma50[-1]
    above20 = sma20[-1] is not None and last > sma20[-1]
    return {"data": data, "rsi": _rsi(closes), "hi": max(closes), "lo": min(closes),
            "last": last, "above_sma50": above50, "above_sma20": above20}


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def recommend(co: Dict, a: Dict) -> Dict:
    v = valuate(co, a)
    f = fundamentals(co)
    t = technicals(co)
    mos = (v["intrinsic"] - co["price"]) / co["price"]
    reasons = []

    valuation = _clamp(50 + mos * 100, 0, 100)
    reasons.append({"label": "Valuation", "score": valuation,
                    "note": f"{mos*100:.1f}% margin of safety vs intrinsic \u20b9{v['intrinsic']:.0f}",
                    "good": mos > 0.1, "bad": mos < -0.1})

    if co["type"] == "financial":
        # Use safe() so None values don't crash — default to neutral values
        gnpa = safe(co.get("nbfc", {}).get("gnpa"), 0.03)
        crar = safe(co.get("nbfc", {}).get("crar"), 0.18)
        roe_val = safe(f["roe"], 0.12)
        roe_s = _clamp((roe_val - 0.10) / 0.15 * 100, 0, 100)
        ap_s  = _clamp((0.05 - gnpa) / 0.05 * 100, 0, 100)
        cap_s = _clamp((crar - 0.15) / 0.15 * 100, 0, 100)
        quality = 0.5 * roe_s + 0.3 * ap_s + 0.2 * cap_s
        qnote = f"ROE {roe_val*100:.1f}%, GNPA {gnpa*100:.2f}%, CRAR {crar*100:.1f}%"
    else:
        roe_val = safe(f["roe"], 0.12)
        roe_s = _clamp((roe_val - 0.10) / 0.15 * 100, 0, 100)
        margin_s = _clamp(safe(a.get("ebit_margin"), 0.12) / 0.20 * 100, 0, 100)
        lev_s = _clamp((0.3 - safe(a.get("debt_weight"), 0.20)) / 0.3 * 100, 0, 100)
        quality = 0.45 * roe_s + 0.35 * margin_s + 0.2 * lev_s
        qnote = f"EBIT margin {safe(a.get('ebit_margin'),0.12)*100:.1f}%"
    reasons.append({"label": "Quality", "score": quality, "note": qnote,
                    "good": quality > 60, "bad": quality < 40})

    momentum = 50
    if t["above_sma50"]: momentum += 18
    if t["above_sma20"]: momentum += 10
    if t["rsi"] > 70: momentum -= 15
    if t["rsi"] < 30: momentum += 8
    momentum = _clamp(momentum, 0, 100)
    reasons.append({"label": "Momentum", "score": momentum,
                    "note": f"{'Above' if t['above_sma50'] else 'Below'} 50-DMA, RSI {t['rsi']:.0f}",
                    "good": t["above_sma50"], "bad": not t["above_sma50"]})

    risk, flags = 0, []
    if co["type"] == "financial":
        gnpa = safe(co.get("nbfc", {}).get("gnpa"), 0.03)
        crar = safe(co.get("nbfc", {}).get("crar"), 0.18)
        if gnpa > 0.04: risk += 25; flags.append("Elevated GNPA")
        if crar < 0.16: risk += 20; flags.append("Thin capital adequacy")
    else:
        if safe(a.get("debt_weight"), 0.20) > 0.4: risk += 25; flags.append("High leverage")
    if mos < -0.25: risk += 15; flags.append("Trading well above intrinsic")
    risk = _clamp(risk, 0, 100)
    risk_score = 100 - risk
    reasons.append({"label": "Risk", "score": risk_score,
                    "note": ", ".join(flags) if flags else "No major flags",
                    "good": len(flags) == 0, "bad": len(flags) >= 2})

    composite = 0.45 * valuation + 0.28 * quality + 0.14 * momentum + 0.13 * risk_score
    verdict = "BUY" if composite >= 65 else "HOLD" if composite >= 45 else "AVOID"
    return {"valuation": v, "fundamentals": f, "technicals": t, "mos": mos,
            "reasons": reasons, "composite": composite, "verdict": verdict}
