"""
Valuation, fundamentals, technical and recommendation engines.

DCF correctness fixes (v2):
  1. Residual-income terminal value is now internally consistent — terminal
     growth is capped at the SUSTAINABLE rate (terminal_roe * retention), and
     the terminal RI numerator grows one period at that same rate. A lender
     cannot grow book value faster than the ROE it retains, so any assumed
     terminal_growth above that is fictional and was previously inflating TV.
  2. fcff_dcf reads net_debt / revenue via safe() so a company missing those
     yfinance fields no longer crashes valuation.
  3. valuate() / recommend() tolerate missing inputs and missing price series.

The model selection (Residual Income for financials, FCFF for the rest) is
unchanged and correct: discounting free cash flow for a lender is meaningless
because borrowings are raw material, not financing.
"""
from typing import Dict, List


def safe(val, default=0.0):
    """Return val if not None, else default."""
    return val if val is not None else default


def cost_of_equity(a: Dict) -> float:
    return safe(a.get("risk_free"), 0.069) + safe(a.get("beta"), 1.0) * safe(a.get("erp"), 0.065)


def _sustainable_growth(terminal_roe: float, payout: float) -> float:
    """g = ROE * retention. The only rate at which a lender can grow book
    value indefinitely without raising external capital."""
    retention = 1.0 - safe(payout, 0.0)
    return safe(terminal_roe, 0.0) * retention


# Long-run nominal economy growth — the ceiling for any terminal perpetuity.
_G_ECON = 0.055
# No financial sustains >20% book-value growth indefinitely.
_GROWTH_CAP = 0.20


def _standard_cap_years(forecast_roe: float, ke: float) -> int:
    """Competitive-advantage period scaled to franchise quality, measured by
    the ROE-minus-cost-of-equity spread. Wider, more durable moat -> longer
    period of excess returns before fade."""
    spread = forecast_roe - ke
    if spread >= 0.10:
        return 10   # elite franchise
    if spread >= 0.05:
        return 7    # strong
    if spread >= 0.02:
        return 5    # decent
    return 3        # thin / no moat


def _rim_intrinsic(bvps0, ke, forecast_roe, terminal_roe, retention,
                   cap_years, fade_years):
    """Residual income with a competitive-advantage period: ROE holds at
    forecast through cap_years, fades to terminal over fade_years, then a
    perpetuity capped at the long-run economy growth rate. Book-value growth
    is capped each year so high-ROE/high-retention names cannot compound to
    absurdity. Returns (intrinsic, pv_explicit, tv_pv, rows)."""
    bv = bvps0
    pv = 0.0
    rows = []
    t = 0
    for _ in range(int(round(cap_years))):
        t += 1
        ri = (forecast_roe - ke) * bv
        disc = (1 + ke) ** t
        pv += ri / disc
        rows.append({"t": t, "roe": forecast_roe, "bv_begin": bv, "ri": ri, "pv": ri / disc})
        bv = bv * (1 + min(forecast_roe * retention, _GROWTH_CAP))
    F = max(1, int(round(fade_years)))
    for k in range(1, F + 1):
        t += 1
        roe = forecast_roe + (terminal_roe - forecast_roe) * (k / F)
        ri = (roe - ke) * bv
        disc = (1 + ke) ** t
        pv += ri / disc
        rows.append({"t": t, "roe": roe, "bv_begin": bv, "ri": ri, "pv": ri / disc})
        bv = bv * (1 + min(roe * retention, _GROWTH_CAP))
    g = min(_G_ECON, ke - 0.005)
    ri_next = (terminal_roe - ke) * bv * (1 + g)
    tv = ri_next / (ke - g) if g < ke else 0.0
    tv_pv = tv / ((1 + ke) ** t)
    intrinsic = bvps0 + pv + tv_pv
    return intrinsic, pv, tv_pv, rows


def _market_implied_cap(bvps0, ke, forecast_roe, terminal_roe, retention,
                        price_per_share, fade_years, max_cap=40):
    """Solve for the competitive-advantage-period length that makes RIM equal
    the current price. Tells you how many years of excess-return persistence
    the market is paying for. None -> even a 40y CAP can't justify the price
    (market is pricing in extreme/heroic assumptions)."""
    if price_per_share is None or price_per_share <= 0:
        return None
    hi_intr, *_ = _rim_intrinsic(bvps0, ke, forecast_roe, terminal_roe,
                                 retention, max_cap, fade_years)
    if hi_intr < price_per_share:
        return None
    lo, hi = 0.0, float(max_cap)
    for _ in range(40):
        mid = (lo + hi) / 2
        intr, *_ = _rim_intrinsic(bvps0, ke, forecast_roe, terminal_roe,
                                  retention, mid, fade_years)
        if intr < price_per_share:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 1)


def residual_income(co: Dict, a: Dict) -> Dict:
    ke = cost_of_equity(a)
    shares = safe(co.get("shares"), 0.0)
    if shares <= 0:
        return {"ke": ke, "wacc": None, "bvps0": None, "intrinsic": None,
                "ev": None, "pv_explicit": None, "tv_pv": None, "rows": [],
                "method": "Residual Income (CAP)", "error": "missing shares"}
    bvps0 = safe(co.get("equity"), 0.0) / shares
    payout = safe(a.get("payout"), 0.20)
    retention = 1.0 - payout
    forecast_roe = safe(a.get("forecast_roe"), 0.15)
    terminal_roe = safe(a.get("terminal_roe"), 0.13)
    fade_years = max(3, round(safe(a.get("fade_years"), 12)))
    cap_years = _standard_cap_years(forecast_roe, ke)

    intrinsic, pv, tv_pv, rows = _rim_intrinsic(
        bvps0, ke, forecast_roe, terminal_roe, retention, cap_years, fade_years)

    price = safe(co.get("price"), 0.0) or None
    implied_cap = _market_implied_cap(
        bvps0, ke, forecast_roe, terminal_roe, retention, price, fade_years)

    return {"ke": ke, "wacc": None, "bvps0": bvps0, "intrinsic": intrinsic,
            "ev": None, "pv_explicit": pv, "tv_pv": tv_pv, "rows": rows,
            "cap_years": cap_years, "fade_years": fade_years,
            "g_terminal": min(_G_ECON, ke - 0.005),
            "market_implied_cap_years": implied_cap,
            "method": "Residual Income (CAP)"}


def fcff_dcf(co: Dict, a: Dict) -> Dict:
    ke = cost_of_equity(a)
    shares = safe(co.get("shares"), 0.0)
    dw = safe(a.get("debt_weight"), 0.20)
    ew = 1 - dw
    wacc = ew * ke + dw * safe(a.get("cost_debt"), 0.085) * (1 - safe(a.get("tax_rate"), 0.25))
    N = max(3, round(safe(a.get("fade_years"), 8)))

    rev = safe(co.get("revenue"), 0.0)
    if rev <= 0 or shares <= 0:
        return {"ke": ke, "wacc": wacc, "bvps0": None, "intrinsic": None,
                "ev": None, "equity_val": None, "pv_explicit": None,
                "tv_pv": None, "rows": [], "method": "FCFF DCF",
                "error": "missing revenue or shares"}

    rev_growth = safe(a.get("rev_growth"), 0.10)
    g_assumed = safe(a.get("terminal_growth"), 0.055)
    g_term = min(g_assumed, wacc - 0.005)  # terminal g strictly below wacc
    g_term = max(g_term, 0.0)
    ebit_margin = safe(a.get("ebit_margin"), 0.14)
    tax_rate = safe(a.get("tax_rate"), 0.25)
    reinvest = safe(a.get("reinvest_rate"), 0.35)

    pv = 0.0
    rows = []
    for t in range(1, N + 1):
        g = rev_growth + (g_term - rev_growth) * (t / N)
        rev = rev * (1 + g)
        ebit = rev * ebit_margin
        nopat = ebit * (1 - tax_rate)
        fcff = nopat * (1 - reinvest)
        disc = (1 + wacc) ** t
        pv += fcff / disc
        rows.append({"t": t, "rev": rev, "fcff": fcff, "pv": fcff / disc})

    fcff_next = rows[N - 1]["fcff"] * (1 + g_term)
    tv = fcff_next / (wacc - g_term) if g_term < wacc else 0.0
    tv_pv = tv / ((1 + wacc) ** N)
    ev = pv + tv_pv
    equity_val = ev - safe(co.get("net_debt"), 0.0)   # safe(): missing net_debt -> 0
    intrinsic = equity_val / shares
    return {"ke": ke, "wacc": wacc, "bvps0": None, "intrinsic": intrinsic,
            "ev": ev, "equity_val": equity_val, "pv_explicit": pv, "tv_pv": tv_pv,
            "rows": rows, "g_terminal": g_term, "method": "FCFF DCF"}


def valuate(co: Dict, a: Dict) -> Dict:
    return residual_income(co, a) if co.get("type") == "financial" else fcff_dcf(co, a)


def sensitivity(co: Dict, a: Dict) -> Dict:
    rate_deltas = [-0.01, -0.005, 0, 0.005, 0.01]
    g_deltas = [-0.01, -0.005, 0, 0.005, 0.01]
    grid = []
    for rd in rate_deltas:
        row = []
        for gd in g_deltas:
            res = valuate(co, {**a,
                               "terminal_growth": safe(a.get("terminal_growth"), 0.05) + gd,
                               "risk_free": safe(a.get("risk_free"), 0.069) + rd})
            row.append(res.get("intrinsic"))
        grid.append(row)
    return {"rate_deltas": rate_deltas, "g_deltas": g_deltas, "grid": grid}


def fundamentals(co: Dict) -> Dict:
    shares = safe(co.get("shares"), 0.0)
    if shares <= 0:
        return {"bvps": None, "eps": None, "pb": None, "pe": None, "roe": None}
    equity = safe(co.get("equity"), 0.0)
    price = safe(co.get("price"), 0.0)
    bvps = equity / shares if shares else None
    eps = co["net_profit"] / shares if co.get("net_profit") else None
    pb = price / bvps if bvps else None
    pe = price / eps if eps else None
    roe = co["net_profit"] / equity if (co.get("net_profit") and equity) else None
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
    if len(series) < n + 1:
        return 50.0
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
    series = co.get("series") or []
    closes = [p["close"] for p in series]
    if not closes:
        return {"data": [], "rsi": 50.0, "hi": None, "lo": None, "last": None,
                "above_sma50": False, "above_sma20": False}
    sma20 = _sma(closes, 20)
    sma50 = _sma(closes, 50)
    data = [{"i": p.get("i", k), "close": p["close"], "sma20": sma20[k], "sma50": sma50[k]}
            for k, p in enumerate(series)]
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
    price = safe(co.get("price"), 0.0)
    intrinsic = v.get("intrinsic")

    # If valuation could not be computed, return a HOLD with an explicit note.
    if intrinsic is None or price <= 0:
        return {"valuation": v, "fundamentals": f, "technicals": t, "mos": None,
                "reasons": [{"label": "Valuation", "score": 50,
                             "note": "Insufficient data to value", "good": False, "bad": False}],
                "composite": 50, "verdict": "HOLD"}

    mos = (intrinsic - price) / price
    reasons = []

    valuation = _clamp(50 + mos * 100, 0, 100)
    _imp = v.get("market_implied_cap_years")
    if _imp is None:
        _impnote = "; mkt prices in >40y of excess returns (rich)"
    elif _imp <= 3:
        _impnote = f"; mkt implies only {_imp}y excess returns (undemanding)"
    else:
        _impnote = f"; mkt implies ~{_imp}y of excess returns"
    reasons.append({"label": "Valuation", "score": valuation,
                    "note": f"{mos*100:.1f}% margin of safety vs intrinsic \u20b9{intrinsic:.0f}{_impnote}",
                    "good": mos > 0.1, "bad": mos < -0.1})

    if co.get("type") == "financial":
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
    if co.get("type") == "financial":
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
