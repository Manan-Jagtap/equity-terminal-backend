"""
Valuation, fundamentals, technical and recommendation engines.
Fixed: handles None values for NBFC metrics on bulk-ingested companies.
"""
from typing import Dict, List
from . import sector_params as SP


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


def _has(*vals) -> bool:
    """True only if every value is present and non-zero where a denominator."""
    return all(v is not None for v in vals)


def valuate(co: Dict, a: Dict) -> Dict:
    """Route to the right model — but only when the required inputs exist.
    Returns intrinsic=None (rather than fabricating) when data is missing, so
    the API never reports a confident value built on invented numbers."""
    try:
        if co["type"] == "financial":
            if not (_has(co.get("equity"), co.get("shares")) and co["equity"] > 0 and co["shares"] > 0):
                return {"intrinsic": None, "method": "Residual Income", "rows": [],
                        "ke": None, "wacc": None, "bvps0": None, "ev": None,
                        "pv_explicit": None, "tv_pv": None}
            return residual_income(co, a)
        if not (_has(co.get("revenue"), co.get("net_debt"), co.get("shares")) and co["shares"] > 0):
            return {"intrinsic": None, "method": "FCFF DCF", "rows": [],
                    "ke": None, "wacc": None, "bvps0": None, "ev": None,
                    "pv_explicit": None, "tv_pv": None}
        return fcff_dcf(co, a)
    except Exception:
        return {"intrinsic": None, "method": "n/a", "rows": [],
                "ke": None, "wacc": None, "bvps0": None, "ev": None,
                "pv_explicit": None, "tv_pv": None}


# ── Relative-method cross-checks (for the blended fair value) ───────────────
# These triangulate the intrinsic model. They use SECTOR-MEDIAN multiples from
# sector_params (not the stock's own spot multiple), so they are a sector-normal
# cross-check rather than a circular "what the market pays for this stock today".

def _vsector(a: Dict) -> str:
    return a.get("_valuation_sector") or SP.DEFAULT_SECTOR


def exit_multiple_value(co: Dict, a: Dict):
    """EV/EBITDA exit-multiple value per share (non-financials). None if N/M."""
    p = SP.params(_vsector(a))
    mult = p.get("exit_ev_ebitda")
    rev, shares = co.get("revenue"), co.get("shares")
    if mult is None or rev is None or not shares or shares <= 0:
        return None
    margin = a.get("ebit_margin") or 0.12
    ebitda = rev * (margin + 0.03)   # EBIT margin + ~3pp D&A add-back → EBITDA proxy
    if ebitda <= 0:
        return None
    ev = ebitda * mult
    net_debt = co.get("net_debt") or 0
    val = (ev - net_debt) / shares
    return val if val > 0 else None


def pe_value(co: Dict, a: Dict):
    """Sector-median P/E value per share. None if loss-making / no data."""
    p = SP.params(_vsector(a))
    pe = p.get("exit_pe")
    pat, shares = co.get("net_profit"), co.get("shares")
    if pe is None or pat is None or pat <= 0 or not shares or shares <= 0:
        return None
    return (pat * pe) / shares


def gordon_pb_value(co: Dict, a: Dict, v: Dict):
    """Justified P/B × BVPS (financials), using terminal ROE & growth.
    P/B* = (ROE_term − g) / (Ke − g), sanity-clamped."""
    ke = v.get("ke"); bvps0 = v.get("bvps0")
    term_roe = a.get("terminal_roe"); g = a.get("terminal_growth") or 0.05
    if not (ke and bvps0 and term_roe) or ke <= g:
        return None
    pb = (term_roe - g) / (ke - g)
    pb = max(0.4, min(pb, 12))
    val = bvps0 * pb
    return val if val > 0 else None


# Triangulation weights. Non-financials lead with the DCF; financials with RI.
_BLEND_WEIGHTS = {
    "fin":    [("Residual Income", 0.65), ("Gordon Growth P/B", 0.20), ("P/E (sector)", 0.15)],
    "nonfin": [("FCFF DCF",        0.55), ("Exit Multiple",     0.30), ("P/E (sector)", 0.15)],
}


def blended(co: Dict, a: Dict) -> Dict:
    """Triangulate the primary intrinsic model with two relative cross-checks and
    return a weighted blended fair value PLUS the per-method breakdown.

    Re-weights over only the methods that actually compute (so a missing P/E
    doesn't drag the blend to zero). Returns blended=None when even the primary
    model can't be valued, so callers show '—' rather than a fabricated number."""
    v = valuate(co, a)
    primary = v.get("intrinsic")
    is_fin = co.get("type") == "financial"

    if is_fin:
        vals = {"Residual Income": primary,
                "Gordon Growth P/B": gordon_pb_value(co, a, v),
                "P/E (sector)": pe_value(co, a)}
        spec = _BLEND_WEIGHTS["fin"]
    else:
        vals = {"FCFF DCF": primary,
                "Exit Multiple": exit_multiple_value(co, a),
                "P/E (sector)": pe_value(co, a)}
        spec = _BLEND_WEIGHTS["nonfin"]

    components = [{"method": name, "value": vals.get(name), "weight": w} for name, w in spec]

    if primary is None or primary <= 0:
        return {"blended": None, "components": components,
                "primary": primary, "primary_method": v.get("method"), "valuation": v}

    avail = [c for c in components if c["value"] is not None and c["value"] > 0]
    wsum = sum(c["weight"] for c in avail) or 1.0
    blend = sum(c["value"] * (c["weight"] / wsum) for c in avail)
    return {"blended": blend, "components": components,
            "primary": primary, "primary_method": v.get("method"), "valuation": v}


def sensitivity(co: Dict, a: Dict) -> Dict:
    rate_deltas = [-0.01, -0.005, 0, 0.005, 0.01]
    g_deltas = [-0.01, -0.005, 0, 0.005, 0.01]
    grid = [[valuate(co, {**a, "terminal_growth": a["terminal_growth"] + gd,
                          "risk_free": a["risk_free"] + rd})["intrinsic"]
             for gd in g_deltas] for rd in rate_deltas]
    return {"rate_deltas": rate_deltas, "g_deltas": g_deltas, "grid": grid}


def fundamentals(co: Dict) -> Dict:
    """N/M-safe. A multiple is returned only when its denominator is positive;
    otherwise None → the UI shows 'N/M' instead of a misleading negative or
    near-zero ratio (e.g. loss-making P/E, negative-equity P/B)."""
    equity = co.get("equity")
    shares = co.get("shares")
    pat    = co.get("net_profit")
    price  = co.get("price")
    bvps = equity / shares if (equity is not None and shares) else None
    eps  = pat / shares if (pat is not None and shares) else None
    pe = price / eps if (eps is not None and eps > 0) else None
    pb = price / bvps if (bvps is not None and bvps > 0) else None
    roe = pat / equity if (pat is not None and equity and equity > 0) else None
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
    from .data_quality import data_quality
    # The headline is the BLENDED fair value (intrinsic model + relative cross-
    # checks), so the verdict/MoS and the screener all agree on one number. The
    # primary model `v` is kept for the projection schedule / TV% display.
    b = blended(co, a)
    v = b["valuation"]
    f = fundamentals(co)
    t = technicals(co)
    conf = data_quality(co)

    iv = b.get("blended")
    iv = iv if (iv is not None and iv > 0) else None
    mos = (iv - co["price"]) / co["price"] if (iv is not None and co["price"]) else None
    reliable = iv is not None and conf["score"] >= 0.5
    reasons = []

    # Gentler MoS\u2192score curve (does not saturate at +50% MoS like the old one).
    valuation = _clamp(50 + mos * 45, 0, 100) if mos is not None else 50
    reasons.append({"label": "Valuation", "score": valuation,
                    "note": (f"{mos*100:.1f}% margin of safety vs intrinsic \u20b9{iv:.0f}"
                             if mos is not None else "Intrinsic value not computable from available data"),
                    "good": mos is not None and mos > 0.15,
                    "bad":  mos is not None and mos < -0.10})

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
    if mos is not None and mos < -0.30: risk += 15; flags.append("Significantly overvalued")
    if co.get("net_profit") is not None and co["net_profit"] < 0: risk += 25; flags.append("Loss-making")
    risk = _clamp(risk, 0, 100)
    risk_score = 100 - risk
    reasons.append({"label": "Risk", "score": risk_score,
                    "note": ", ".join(flags) if flags else "No major flags",
                    "good": len(flags) == 0, "bad": len(flags) >= 2})

    raw = 0.42 * valuation + 0.28 * quality + 0.16 * momentum + 0.14 * risk_score
    # Scale by data confidence — weak data can never produce a strong score.
    composite = raw * (0.6 + 0.4 * conf["score"]) if reliable else raw * 0.5

    # Verdict scheme (no "TRIM"): BUY / ACCUMULATE / HOLD / REDUCE / AVOID,
    # plus the two data-state sentinels. This is the INDEPENDENT model's own
    # view from margin of safety + composite quality — analyst consensus is
    # surfaced separately and never blended in here.
    if iv is None:                              verdict = "NO DATA"
    elif conf["score"] < 0.5:                   verdict = "LOW CONF"
    elif composite >= 68 and mos > 0.15:        verdict = "BUY"
    elif composite >= 58 and mos > 0.05:        verdict = "ACCUMULATE"
    elif mos >= -0.10:                          verdict = "HOLD"
    elif mos >= -0.25:                          verdict = "REDUCE"
    else:                                       verdict = "AVOID"

    return {"valuation": v, "fundamentals": f, "technicals": t, "mos": mos,
            "intrinsic": iv, "confidence": conf, "reliable": reliable,
            "reasons": reasons, "composite": composite, "verdict": verdict,
            # Blended fair value breakdown — the per-method values + weights the
            # Valuation tab renders, plus the pure intrinsic-model value as one input.
            "blended": iv, "components": b.get("components"),
            "dcf_value": b.get("primary"), "primary_method": b.get("primary_method"),
            "drivers": a.get("_drivers"), "valuation_sector": a.get("_valuation_sector")}
