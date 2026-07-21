"""
Valuation, fundamentals, technical and recommendation engines.
Fixed: handles None values for NBFC metrics on bulk-ingested companies.
"""
from typing import Dict, List
from . import sector_params as SP

# Diversified conglomerates / incubators that no single-sector model can value —
# their parts trade on different economics, so the blended DCF/RI is wrong by
# construction and they're surfaced as LOW CONF (need sum-of-the-parts).
# Diversified conglomerates + pure holding companies: a single-sector engine is
# wrong by construction (the parts trade on different economics; holdcos trade
# at a discount to look-through NAV that no P&L model sees). SOTP presets exist
# for the first two; the holdcos read NO CALL until a NAV model lands.
_CONGLOMERATES = {"RELIANCE", "ADANIENT", "BBTC", "TATAINVEST", "BAJAJHLDNG", "CHOLAHLDNG"}

# Asset-light FEE financials: exchanges, depositories, registrars, AMCs, broking,
# wealth, data/ratings. Their value is a capital-light fee annuity, not book
# equity — they trade at 8-12x book, so the book-based Residual Income model
# structurally understates them exactly like life insurers. Flag → LOW CONF
# rather than a confident AVOID (a P/E or DCF model would be the right tool).
_FEE_FINANCIALS = {"CRISIL", "ICRA", "CARERATNG", "BSE", "MCX", "IEX", "CDSL",
                   "CAMS", "KFINTECH", "NUVAMA", "ANGELONE", "360ONE", "POLICYBZR",
                   "PAYTM", "ABSLAMC", "UTIAMC", "HDFCAMC", "NAM-INDIA", "MFSL",
                   "IIFL", "IIFLCAPS", "ANANDRATHI", "PRUDENT", "CDGL"}

# The GENERAL rule behind the ticker list: any capital-markets FEE business —
# broking, wealth, asset management, exchanges/depositories, ratings, market
# infrastructure — earns a capital-light fee annuity, so a book/DCF model
# mis-values it (Anand Rathi, a broker, printed a DCF BUY at +190%). Match on the
# raw sector too, so new/unlisted-in-the-set names are caught automatically.
_FEE_FINANCIAL_SECTOR_HINTS = (
    "investment services", "capital market", "asset management", "broking",
    "brokerage", "wealth", "stock exchange", "exchange", "depositor",
    "rating", "market infrastructure", "financial products distribution")


def _is_fee_financial(co: Dict) -> bool:
    if (co.get("ticker") or "").upper() in _FEE_FINANCIALS:
        return True
    sec = (co.get("sector") or "").lower()
    return any(h in sec for h in _FEE_FINANCIAL_SECTOR_HINTS)


def safe(val, default=0.0):
    """Return val if not None, else default."""
    return val if val is not None else default


def cost_of_equity(a: Dict) -> float:
    return a["risk_free"] + a["beta"] * a["erp"]


def residual_income(co: Dict, a: Dict) -> Dict:
    """Two-stage Residual Income (excess-return) model for financials.

    Stage 1 (high-return phase, ~half the horizon): the franchise EARNS its
    forecast ROE and book value compounds at ROE × retention. This is what a
    single immediate-fade model missed — India's best banks/NBFCs sustain a
    decade-plus of mid-teens ROE and ~12-15% book growth, which is exactly what
    justifies their 2-2.5x P/B. Fading ROE from year 1 understated them badly.

    Stage 2 (fade): ROE glides from the franchise level to the sector-mature
    terminal ROE. Terminal: RI perpetuity at terminal ROE & growth."""
    ke = cost_of_equity(a)
    bvps0 = co["equity"] / co["shares"]
    # Asset-quality adjustment (institutional standard, e.g. ICICI's adj-book):
    # net NPAs still on the book will likely need further write-downs, so haircut
    # the starting equity. nnpa is a ratio; nnpa × (advances/equity ~6) × LGD
    # ~0.67 ≈ nnpa × 4 as a fraction of equity, capped 20%. Healthy banks (nnpa≈0)
    # are untouched; stressed books carry a real, bounded haircut.
    _nnpa = co.get("nbfc", {}).get("nnpa")
    aq_haircut = min(0.20, max(0.0, _nnpa) * 4) if isinstance(_nnpa, (int, float)) else 0.0
    if aq_haircut:
        bvps0 = bvps0 * (1 - aq_haircut)
        (a.setdefault("_drivers", {}))["adj_book"] = (
            f"book haircut {aq_haircut*100:.0f}% for net NPA {_nnpa*100:.2f}% (asset quality)")
    retention = 1 - a["payout"]
    N = max(3, round(a["fade_years"]))
    N1 = max(1, round(0.6 * N))             # high-ROE hold phase (was N//2 — too
                                            # short; elite franchises defend their
                                            # ROE longer before the fade begins)
    f_roe, t_roe = a["forecast_roe"], a["terminal_roe"]
    bv, pv, rows = bvps0, 0.0, []
    for t in range(1, N + 1):
        roe = f_roe if t <= N1 else f_roe + (t_roe - f_roe) * ((t - N1) / (N - N1))
        ri = (roe - ke) * bv
        disc = (1 + ke) ** t
        pv += ri / disc
        rows.append({"t": t, "roe": roe, "bv_begin": bv, "ri": ri, "pv": ri / disc})
        bv = bv * (1 + roe * retention)
    ri_next = (t_roe - ke) * bv
    tv = ri_next / (ke - a["terminal_growth"]) if a["terminal_growth"] < ke else 0.0
    tv_pv = tv / ((1 + ke) ** N)
    intrinsic = bvps0 + pv + tv_pv
    return {"ke": ke, "wacc": None, "bvps0": bvps0, "intrinsic": intrinsic,
            "ev": None, "pv_explicit": pv, "tv_pv": tv_pv, "rows": rows,
            "method": "Residual Income"}


def fcff_dcf(co: Dict, a: Dict) -> Dict:
    """Two-stage FCFF DCF, mirroring the RI design.

    Stage 1 (franchise phase, ~half the horizon): revenue grows at the FULL
    derived near-term rate. Fading from year 1 — the old behaviour — silently
    cut year-1 growth by 1/N and priced even durable compounders as if their
    growth advantage started dying immediately, which is the main reason the
    model printed AVOID across India's quality cohort.

    Stage 2: linear fade from the stage-1 rate to terminal growth, landing
    exactly on g_t in year N. The horizon N itself is quality-dependent
    (derive.py sets fade_years from ROIC durability — the competitive-
    advantage period), so moats get more years of franchise growth, not a
    fudged multiple."""
    ke = cost_of_equity(a)
    ew = 1 - a["debt_weight"]
    wacc = ew * ke + a["debt_weight"] * a["cost_debt"] * (1 - a["tax_rate"])
    # HARD FLOOR: WACC must stay a safe margin above terminal growth. Asset-light
    # retailers/jewellers carry large working-capital / gold-metal "borrowings"
    # that the debt_weight reads as cheap leverage, collapsing WACC toward g and
    # exploding the terminal value (TITAN printed +3000% MoS without this floor).
    wacc = max(wacc, a["terminal_growth"] + 0.03)
    N = max(3, round(a["fade_years"]))
    N1 = max(1, N // 2)                     # franchise (hold) phase length
    g_t = a["terminal_growth"]
    g1 = a["rev_growth"]
    rev, pv, rows = co["revenue"], 0.0, []
    nopat = 0.0
    m0 = a["ebit_margin"]
    m_term = a.get("terminal_ebit_margin", m0)         # margins mean-revert; glide
    for t in range(1, N + 1):
        g = g1 if t <= N1 else g1 + (g_t - g1) * ((t - N1) / (N - N1))
        rev = rev * (1 + g)
        margin = m0 if N <= 1 else m0 + (m_term - m0) * ((t - 1) / (N - 1))
        ebit = rev * margin
        nopat = ebit * (1 - a["tax_rate"])
        fcff = nopat * (1 - a["reinvest_rate"])
        disc = (1 + wacc) ** t
        pv += fcff / disc
        rows.append({"t": t, "rev": rev, "fcff": fcff, "pv": fcff / disc})
    # Terminal value must use the STEADY-STATE reinvestment rate (terminal_growth
    # / mature ROIC), NOT the explicit-period rate. A high-growth company can
    # reinvest 60–80% during the forecast, but in perpetuity at ~4–6% growth it
    # only needs g/ROIC (~30%). Reusing the explicit rate forever understated the
    # terminal FCFF by 2–3×, which was the dominant reason intrinsics came out
    # far below market price. (This matches the client DCF, which already does it.)
    mature_roic = SP.params(a.get("_valuation_sector")).get("mature_roic") or 0.12
    term_rr = max(0.0, min(g_t / mature_roic, 0.75)) if mature_roic else a["reinvest_rate"]
    fcff_terminal = nopat * (1 + g_t) * (1 - term_rr)
    tv = fcff_terminal / (wacc - g_t) if g_t < wacc else 0.0
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
    """Sector EV/EBITDA on 1-year-FORWARD EBITDA (standard forward-multiple
    convention), per share. A rich sector 'exit' multiple on a trailing metric is
    a spot-relative anchor; forward is the right horizon. It is still only a
    sanity CROSS-CHECK — blended() bands it around the DCF so it can't override
    the intrinsic (the sector exit multiples are rich and read ~2× the DCF)."""
    p = SP.params(_vsector(a))
    mult = p.get("exit_ev_ebitda")
    rev, shares = co.get("revenue"), co.get("shares")
    if mult is None or rev is None or not shares or shares <= 0:
        return None
    margin = a.get("ebit_margin") or 0.12
    ebitda_fwd = rev * (1 + (a.get("rev_growth") or 0.08)) * (margin + 0.03)
    if ebitda_fwd <= 0:
        return None
    ev = ebitda_fwd * mult
    net_debt = co.get("net_debt") or 0
    val = (ev - net_debt) / shares
    return val if val > 0 else None


def pe_value(co: Dict, a: Dict):
    """Sector P/E on 1-year-FORWARD earnings, per share. None if loss-making.
    For financials the sector P/E is a GROWTH-lender multiple, so it's scaled by
    realized-vs-mature ROE — a 13.5%-ROE HFC is not worth an 18x Bajaj-Finance P/E."""
    p = SP.params(_vsector(a))
    pe = p.get("exit_pe")
    pat, shares = co.get("net_profit"), co.get("shares")
    if pe is None or pat is None or pat <= 0 or not shares or shares <= 0:
        return None
    if co.get("type") == "financial":
        eq = co.get("equity")
        roe = (pat / eq) if (eq and eq > 0) else None
        mature = p.get("mature_roe") or 0.15
        if roe is not None and mature:
            pe = pe * max(0.4, min(1.0, roe / mature))
    pat_fwd = pat * (1 + (a.get("rev_growth") or 0.08))
    return (pat_fwd * pe) / shares


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


def ddm_value(co: Dict, a: Dict, v: Dict):
    """Two-stage Dividend Discount Model, per share. Dividends grow along the
    same stage-1→terminal path the primary model uses (capped so we never model
    a payer out-growing its earnings), discounted at the cost of equity, with a
    Gordon terminal. Meaningful only for stable, high payers — blended() weights
    it there and shows it as an unweighted cross-check everywhere else."""
    ke = v.get("ke")
    pat, shares = co.get("net_profit"), co.get("shares")
    payout = a.get("payout")
    g_t = a.get("terminal_growth") or 0.05
    if not ke or ke <= g_t or not shares or shares <= 0:
        return None
    if pat is None or pat <= 0 or not payout or payout <= 0:
        return None
    dps0 = payout * (pat / shares)
    if dps0 <= 0:
        return None
    N = max(3, round(a.get("fade_years") or 10))
    N1 = max(1, N // 2)
    g1 = min(a.get("rev_growth") or 0.08, 0.15)   # a payer can't out-grow earnings forever
    d, dN, pv = dps0, dps0, 0.0
    for i in range(1, N + 1):
        g = g1 if i <= N1 else g1 + (g_t - g1) * ((i - N1) / max(1, N - N1))
        d *= (1 + g)
        pv += d / ((1 + ke) ** i)
        if i == N:
            dN = d
    pv += (dN * (1 + g_t) / (ke - g_t)) / ((1 + ke) ** N)
    return pv if pv > 0 else None


def _is_high_payout(co: Dict, a: Dict) -> bool:
    """A stable, meaningful distributor — utilities, PSUs, mature FMCG, high-
    payout financials. Only for these does the DDM carry blend weight; the gate
    is the (3-yr median) payout ratio, so it's objective and self-updating."""
    pat = co.get("net_profit")
    return (a.get("payout") or 0) >= 0.40 and pat is not None and pat > 0


# Triangulation weights. Non-financials lead with the DCF; financials with RI.
# The DCF/RI always leads at 0.55/0.65; the cross-check split is SECTOR-APPROPRIATE
# (from the institutional-model study): capital-heavy businesses are valued on
# EV/EBITDA, asset-light ones on P/E — so we weight the right multiple higher.
_BLEND_WEIGHTS = {
    "fin":    [("Residual Income", 0.65), ("Gordon Growth P/B", 0.20), ("P/E (sector)", 0.15)],
    # FIX-11: DCF leads harder (.55→.65) and the exit multiple is trusted less
    # (.30→.20) — the exit leg was the single biggest source of rich confident
    # calls (CORR-1); the intrinsic DCF is the more defensible anchor.
    "nonfin": [("FCFF DCF",        0.65), ("Exit Multiple",     0.20), ("P/E (sector)", 0.15)],
    # Asset-light: earnings-based P/E is the meaningful multiple; EV/EBITDA less so.
    "nonfin_light": [("FCFF DCF",  0.55), ("Exit Multiple",     0.15), ("P/E (sector)", 0.30)],
}
# Sectors whose value is asset-light / earnings-driven → lean the cross-check on P/E.
_ASSET_LIGHT = {"IT_SERVICES", "CONSUMER", "CONSUMER_DISC", "PHARMA"}


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
        spec = list(_BLEND_WEIGHTS["fin"])
    elif _vsector(a) in _ASSET_LIGHT:
        vals = {"FCFF DCF": primary,
                "Exit Multiple": exit_multiple_value(co, a),
                "P/E (sector)": pe_value(co, a)}
        spec = list(_BLEND_WEIGHTS["nonfin_light"])
    else:
        vals = {"FCFF DCF": primary,
                "Exit Multiple": exit_multiple_value(co, a),
                "P/E (sector)": pe_value(co, a)}
        spec = list(_BLEND_WEIGHTS["nonfin"])

    # Dividend Discount Model — always computed as a cross-check; carries weight
    # only for stable high payers (else weight 0 → shown, not blended).
    vals["Dividend Discount"] = ddm_value(co, a, v)
    spec.append(("Dividend Discount", 0.20 if _is_high_payout(co, a) else 0.0))

    primary_method = v.get("method")

    if primary is None or primary <= 0:
        components = [{"method": name, "value": vals.get(name), "weight": w} for name, w in spec]
        return {"blended": None, "components": components,
                "primary": primary, "primary_method": primary_method, "valuation": v}

    # The intrinsic DCF/RI leads. The relative multiples are a SANITY BAND — they
    # can corroborate or nudge the primary, but a rich sector exit multiple (which
    # reads ~2× the DCF on premium sectors) must not be able to drag the blend far
    # above the intrinsic. Clamp each cross-check to [0.6, 1.6]× the primary before
    # weighting (FIX-11: tightened from [0.5, 2.2] — the wide upper band let a rich
    # exit multiple inflate confident calls). The raw value is still shown in the breakdown.
    LO, HI = 0.6 * primary, 1.6 * primary
    components = []
    for name, w in spec:
        raw = vals.get(name)
        capped = raw
        if raw is not None and name != primary_method:
            capped = max(LO, min(HI, raw))
        components.append({"method": name, "value": raw, "capped": capped, "weight": w})

    avail = [c for c in components if c["capped"] is not None and c["capped"] > 0]
    wsum = sum(c["weight"] for c in avail) or 1.0
    blend = sum(c["capped"] * (c["weight"] / wsum) for c in avail)
    return {"blended": blend, "components": components,
            "primary": primary, "primary_method": primary_method, "valuation": v}


def sensitivity(co: Dict, a: Dict) -> Dict:
    rate_deltas = [-0.01, -0.005, 0, 0.005, 0.01]
    g_deltas = [-0.01, -0.005, 0, 0.005, 0.01]
    grid = [[valuate(co, {**a, "terminal_growth": a["terminal_growth"] + gd,
                          "risk_free": a["risk_free"] + rd})["intrinsic"]
             for gd in g_deltas] for rd in rate_deltas]
    return {"rate_deltas": rate_deltas, "g_deltas": g_deltas, "grid": grid}


# ── FIX-13: conviction legs (the FM contract) ────────────────────────────────
def method_dispersion(components) -> float | None:
    """Max/min across the WEIGHTED, positive blended method values — how far the
    engine's OWN methods disagree. None when fewer than two methods compute."""
    vals = [c.get("value") for c in (components or [])
            if c.get("value") and c["value"] > 0 and (c.get("weight") or 0) > 0]
    return (max(vals) / min(vals)) if len(vals) >= 2 else None


def sensitivity_swing(co: Dict, a: Dict, base_iv) -> float | None:
    """Fractional spread of the primary intrinsic under a ±1% adverse/favourable
    move in BOTH the discount rate and terminal growth (the two levers the DCF is
    most sensitive to). High swing = the fair value isn't robust. Two extra
    valuate calls; guarded so it never breaks a recommendation."""
    if not base_iv or base_iv <= 0:
        return None
    try:
        bear = valuate(co, {**a, "risk_free": a["risk_free"] + 0.01,
                            "terminal_growth": a["terminal_growth"] - 0.01}).get("intrinsic")
        bull = valuate(co, {**a, "risk_free": a["risk_free"] - 0.01,
                            "terminal_growth": a["terminal_growth"] + 0.01}).get("intrinsic")
    except Exception:
        return None
    if bear is None or bull is None:
        return None
    return abs(bull - bear) / base_iv


def tv_share(v) -> float | None:
    """Terminal value as a share of the model value — how much sits beyond the
    explicit forecast (the speculative part). MUST divide by same-unit components:
    fcff returns tv_pv/pv_explicit in EV terms (bvps0 None) → tv/(pv+tv)=tv/EV;
    RI is per-share with bvps0 → tv/(bvps0+pv+tv)=tv/intrinsic. Dividing by the
    per-share `intrinsic` directly would mix EV with per-share and blow up."""
    # DCF-only concept: fcff returns bvps0=None. A financial's RI value is anchored
    # by book (bvps0 set) and its terminal excess return can be negative, so a
    # "TV share" isn't meaningful there → None.
    tv, pv = v.get("tv_pv"), v.get("pv_explicit")
    if tv is None or pv is None or v.get("bvps0") is not None:
        return None
    denom = pv + tv
    return (tv / denom) if denom > 0 else None


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
    pe = price / eps if (price and eps is not None and eps > 0) else None
    pb = price / bvps if (price and bvps is not None and bvps > 0) else None
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
    if len(series) < n + 1:        # not enough points to seed RSI → neutral
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
    pts = [p for p in (co.get("series") or []) if p.get("close") is not None]
    closes = [p["close"] for p in pts]
    if not closes:                 # no price history → neutral, no crash
        return {"data": [], "rsi": 50.0, "hi": None, "lo": None, "last": None,
                "above_sma50": False, "above_sma20": False}
    sma20 = _sma(closes, 20)
    sma50 = _sma(closes, 50)
    # Index SMAs against the FILTERED points — enumerating the raw series while
    # the SMAs were computed on the filtered closes was an IndexError waiting to
    # happen the moment any stored close is NULL.
    data = [{"i": p["i"], "close": p["close"], "sma20": sma20[k], "sma50": sma50[k]}
            for k, p in enumerate(pts)]
    last = closes[-1]
    above50 = sma50[-1] is not None and last > sma50[-1]
    above20 = sma20[-1] is not None and last > sma20[-1]
    return {"data": data, "rsi": _rsi(closes), "hi": max(closes), "lo": min(closes),
            "last": last, "above_sma50": above50, "above_sma20": above20}


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


# Methods produced by alt_models — their divergence gate fires at +60% (the
# hand-seeded inputs are the likeliest broken thing at that distance).
ALT_METHODS = {"Sum-of-the-Parts", "EV + VNB Appraisal", "P/EV Appraisal",
               "Combined-ratio P/B"}


def price_gates(verdict: str, mos, *, is_financial: bool, is_alt: bool,
                high_roe: bool) -> str:
    """VAL-03: the PRICE-DEPENDENT plausibility gates in ONE place, applied by
    BOTH the batch engine (recommend) and the intraday refresh (refresh_mos).
    A price tick moves mos, and mos alone can carry a verdict across one of
    these cliffs — the intraday mirror previously reproduced only the base
    bands, so a batch BUY whose price crashed intraday could sit at +140% MoS
    and stay BUY (the exact state the batch engine forbids).

    Only the HARD cliffs live here (pure functions of mos + stable flags).
    The +50% corroboration band (VAL-01) is deliberately batch-only: it needs
    the per-method component values, which a price tick doesn't change."""
    if mos is None:
        return verdict
    # Lender divergence: a bank/NBFC the model values 80%+ above market is a
    # disagreement to be humble about, never a confident BUY.
    if is_financial and mos >= 0.80 and verdict in ("BUY", "ACCUMULATE"):
        return "LOW CONF"
    # Alt-model divergence (illustrative hand-seeded inputs): stale seed is the
    # likeliest explanation at +60%.
    if is_alt and mos > 0.60 and verdict in ("BUY", "ACCUMULATE"):
        return "LOW CONF"
    # Generic implausible upside: a one-size sector model claiming a double is
    # wrong more often than the market on a liquid name.
    if not is_alt and mos > 1.0 and verdict in ("BUY", "ACCUMULATE", "HOLD",
                                                "REDUCE", "AVOID"):
        return "LOW CONF"
    # Extreme downside on a high-return franchise: far more likely the model
    # structurally understates a premium compounder than a real overvaluation.
    if mos < -0.45 and high_roe and verdict == "AVOID":
        return "LOW CONF"
    return verdict


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

    # Dedicated models for names the single-engine blend can't value (conglomerate
    # Sum-of-the-Parts, life-insurer P/EV). They live OUTSIDE the parity-tested
    # core (blended/valuate ↔ engine.js), so they OVERRIDE the intrinsic here
    # without any JS mirror. Illustrative inputs → capped at MEDIUM confidence below.
    from .alt_models import alternative_intrinsic
    alt = alternative_intrinsic(co, a)
    if alt and alt.get("intrinsic") and alt["intrinsic"] > 0:
        iv = alt["intrinsic"]
    else:
        alt = None
    method = alt["method"] if alt else b.get("primary_method")

    # A synthetic (sentinel) price must never produce a margin of safety — the
    # ₹1.0 placeholder would fabricate an absurd +N×10⁴% MoS. No real price →
    # mos None → verdict NO DATA, which is the honest state.
    has_real_price = bool(co.get("price")) and not co.get("synthetic_price")
    mos = (iv - co["price"]) / co["price"] if (iv is not None and has_real_price) else None
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
        # Use safe() so None values don't crash — default to neutral values.
        # (co.get("nbfc") may be None, not just missing — `or {}` guards that.)
        _nbfc = co.get("nbfc") or {}
        gnpa = safe(_nbfc.get("gnpa"), 0.03)
        crar = safe(_nbfc.get("crar"), 0.18)
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

    # Momentum stays NEUTRAL when the price series is synthetic / absent — the
    # synthetic series trends gently upward by construction, so reading momentum
    # off it would fabricate a bullish (above-50-DMA) signal on names with no real
    # OHLC. Only score momentum on real history.
    if co.get("synthetic_series") or t.get("last") is None:
        momentum = 50
        reasons.append({"label": "Momentum", "score": momentum,
                        "note": "Insufficient real price history — momentum neutral",
                        "good": False, "bad": False})
    else:
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
        _nbfc = co.get("nbfc") or {}
        gnpa = safe(_nbfc.get("gnpa"), 0.03)
        crar = safe(_nbfc.get("crar"), 0.18)
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
    # A genuinely HIGH-RETURN franchise (≥16% ROE — reported, or the derived
    # franchise ROE for a bank/NBFC): the sector DCF is the leg known to
    # understate quality, so such a name should read a real REDUCE ("richly
    # valued, trim") through the moderate-discount zone rather than a confident
    # AVOID; only an EXTREME discount (< −45%) drops it to LOW CONF (below).
    _high_roe = ((f.get("roe") or 0) >= 0.16
                 or (a.get("_valuation_sector") in ("BANK", "NBFC")
                     and max(a.get("forecast_roe") or 0, a.get("terminal_roe") or 0) >= 0.16))
    # FIX-18 (CORR-5/VAL-10): the ladder was re-banded EMPIRICALLY against the
    # 1,001-name independent groundtruth, not by intuition. Two changes survived
    # measurement; the plan's other prescriptions were tested and REJECTED because
    # they lowered agreement / broke the Agree set (see the confusion matrix in
    # PR #31): HOLD ±12 (−5pts agreement, 6 hard breaks), a leg-corroboration
    # condition on BUY (no-op — mistaken BUYs are 19/19 leg-corroborated; the
    # legs share fundamentals), and AVOID-from-−0.35 (the groundtruth is MORE
    # bearish than the engine, not less).
    #   · AVOID starts at −18% (was −25%): the independent read calls AVOID at a
    #     median −21% — the −18..−25 slab was the largest single error cell.
    #   · BUY caps at +50%: beyond that the size of the claimed upside is itself
    #     the risk, so the top label steps down to ACCUMULATE (same POS zone —
    #     an escalation ladder with VAL-01: >50% needs corroboration, >100%
    #     abstains, and >50% never wears the strongest label).
    if iv is None:                              verdict = "NO DATA"
    elif mos is None:                           verdict = "NO DATA"   # have intrinsic but no usable price
    elif conf["score"] < 0.5:                   verdict = "LOW CONF"
    elif composite >= 68 and 0.15 < mos <= 0.50: verdict = "BUY"
    elif composite >= 58 and mos > 0.05:        verdict = "ACCUMULATE"
    elif mos >= -0.10:                          verdict = "HOLD"
    elif mos >= -0.18:                          verdict = "REDUCE"
    elif mos >= -0.45 and _high_roe:            verdict = "REDUCE"
    else:                                       verdict = "AVOID"

    # LENDER model-vs-market divergence gate. A bank/NBFC the independent model
    # values 80%+ above the market price is a large disagreement: the RI model is
    # trusting a forecast ROE the market is clearly discounting for structural
    # reasons it can't see (LIC HF: model ~1.5x book vs a 0.73x market). That is NOT
    # a confident BUY — drop it to the honest LOW CONF / "no confident call" state
    # (with the analyst consensus surfaced) rather than a fabricated high upside.
    #
    # FIX-10 (corroboration-aware, ZERO gate loosening): a ≥80%-MoS lender call is
    # KEPT only when a SECOND, independent leg agrees — the Gordon-growth justified
    # P/B value also lands ≥ 1.25× the market price. Two methods pointing the same
    # way is real corroboration (it converts the wrongly-abstained PSU lenders —
    # PFC/RECLTD/LICHSGFIN — that the RI *and* the P/B both value cheap), not a
    # weakened threshold: absent the second leg, still LOW CONF exactly as before.
    if (co.get("type") == "financial" and mos is not None and mos >= 0.80
            and verdict in ("BUY", "ACCUMULATE")):
        _gpb = gordon_pb_value(co, a, v)
        _price = co.get("price")
        _corroborated = (_gpb is not None and _price and _price > 0
                         and _gpb >= 1.25 * _price)
        if _corroborated:
            reasons.append({"label": "Corroboration", "score": 70,
                            "note": (f"Two independent legs agree cheap: RI/blend {mos*100:.0f}% "
                                     f"below intrinsic AND Gordon-growth justified P/B ₹{_gpb:.0f} "
                                     f"≥ 1.25× price ₹{_price:.0f}. Call kept (not gate-cleared)."),
                            "good": True, "bad": False})
        else:
            verdict = "LOW CONF"

    # A dedicated model (SOTP for conglomerates, P/EV for insurers) replaced the
    # single-engine intrinsic above → keep the computed verdict, but CAP confidence
    # at MEDIUM (the inputs are illustrative) and surface the method + caveat, so
    # it is never presented as a precise, high-confidence call.
    if alt:
        if conf.get("level") == "high":
            conf = {**conf, "level": "medium"}
            if conf["score"] > 0.79:
                conf = {**conf, "score": 0.79}
        reasons.append({"label": "Model", "score": 60, "note": alt["note"],
                        "good": False, "bad": False})
        # DAT-03 divergence guard (mirror of the lender gate): these models run
        # on ILLUSTRATIVE hand-seeded inputs (SOTP segment EVs, insurer EV/VNB).
        # When such a model lands >60% above the market, the likeliest broken
        # thing is the seed, not the market — caught live: ITC's preset
        # cigarettes EV alone exceeded the company's entire market cap, printing
        # "BUY +113%" off a stale constant. Honest state: LOW CONF, keep the
        # method + components visible so the user can judge the inputs.
        if mos is not None and mos > 0.60 and verdict in ("BUY", "ACCUMULATE"):
            verdict = "LOW CONF"
            reliable = False
            reasons.append({"label": "Model", "score": 50,
                            "note": f"Illustrative-input model {mos*100:.0f}% above market — "
                                    "hand-seeded segment/EV values are the likeliest thing "
                                    "to be stale at this divergence. Not a confident call.",
                            "good": False, "bad": True})
    # Life insurers WITHOUT seeded EV can't be valued on book equity / reported
    # earnings — their worth is EMBEDDED VALUE (future profit on in-force policies),
    # which isn't on the balance sheet. RI / P-B / P-E all structurally understate
    # them (they legitimately trade at 7–13x book). Rather than show a confident
    # AVOID, mark the model unreliable so the verdict reads LOW CONF.
    elif a.get("_valuation_sector") == "INSURANCE":
        verdict = "LOW CONF"
        reliable = False
        reasons.append({"label": "Model", "score": 50,
                        "note": "Life insurer — value is embedded value, not book; "
                                "RI/P-B/P-E understate it. Model not reliable here.",
                        "good": False, "bad": True})
    elif _is_fee_financial(co):
        verdict = "LOW CONF"
        reliable = False
        reasons.append({"label": "Model", "score": 50,
                        "note": "Capital-markets fee business (broking/AMC/exchange/"
                                "registrar/ratings) — value is a capital-light fee "
                                "annuity, not book or a spot-earnings DCF; those models "
                                "mis-value it. Read the P/E and analyst view instead.",
                        "good": False, "bad": True})
    elif f["roe"] is not None and f["roe"] < 0.04:
        # Negligible OR NEGATIVE current returns (early-stage / pre-profit growth
        # names like Eternal/Zomato, Jio Financial; and outright loss-makers). A
        # DCF/RI built on near-zero or negative earnings is meaningless — a
        # loss-maker is MORE unreliable than a 3%-ROE name, so it shouldn't get a
        # confident AVOID either. (The old 0 < roe bound let negatives fall
        # through to a "reliable" AVOID.)
        verdict = "LOW CONF"
        reliable = False
        reasons.append({"label": "Model", "score": 50,
                        "note": "Negligible or negative current earnings — intrinsic "
                                "model unreliable on near-zero/negative ROE.",
                        "good": False, "bad": True})
    elif co.get("ticker") in _CONGLOMERATES:
        # Diversified holding companies / incubators (Reliance: oil+telecom+retail;
        # Adani Enterprises: an incubator). No single sector model fits — the parts
        # trade on completely different economics, so a one-engine DCF is wrong by
        # construction (it ignores Jio/Retail for RIL, the pipeline for ADANIENT).
        # These need sum-of-the-parts, which we don't model — so flag, don't AVOID.
        verdict = "LOW CONF"
        reliable = False
        reasons.append({"label": "Model", "score": 50,
                        "note": "Diversified conglomerate — needs sum-of-the-parts; a "
                                "single-sector DCF understates it. Model unreliable here.",
                        "good": False, "bad": True})
    elif mos is not None and mos > 1.0:
        # An intrinsic more than DOUBLE the market price out of a GENERIC sector
        # model is far more likely a mis-modeled name than a hidden multi-bagger:
        # thin-margin distributors/refiners/sugar on premium sector multiples,
        # holding companies, demerger stubs, peak-cycle earnings capitalised as
        # permanent. The market is sometimes wrong; a generic model claiming
        # +100% on a liquid name is wrong more often. Honest state: LOW CONF,
        # never a confident BUY. (History: the old 2.0 gate caught the +700-850%
        # cohort but let REDINGTON +173% / SENCO +197% / CHENNPETRO +119% render
        # as confident BUYs — DAT-02 tightened it to 1.0. Dedicated alt-models
        # (SOTP/insurers) exit this chain earlier and are unaffected.)
        verdict = "LOW CONF"
        reliable = False
        reasons.append({"label": "Model", "score": 50,
                        "note": f"Implausible margin of safety ({mos*100:.0f}%) — the sector "
                                "model likely doesn't fit this name's economics. "
                                "Model unreliable here.",
                        "good": False, "bad": True})
    elif mos is not None and mos < -0.45 and _high_roe:
        # Mirror of the +200% gate, for extreme DOWNSIDE — keyed off the DERIVED
        # franchise ROE (forecast/terminal), not just a noisy single reported year.
        # A model valuing a genuinely HIGH-RETURN franchise (≥18% ROE) more than
        # 35% below price is far more likely STRUCTURALLY UNDERSTATING a premium
        # compounder than flagging a real overvaluation — the false-AVOID cohort
        # (Nestlé, HUL, Bajaj Finance, Colgate…) that the sector DCF can't justify.
        # It reads LOW CONF, not a confident AVOID, until the DCF fixes fully land.
        # Low-return names at a big premium keep their AVOID (genuinely rich).
        verdict = "LOW CONF"
        reliable = False
        reasons.append({"label": "Model", "score": 50,
                        "note": f"Model values a high-return franchise {mos*100:.0f}% below "
                                "price — a single-sector DCF often understates premium "
                                "compounders. Model unreliable here.",
                        "good": False, "bad": True})

    # VAL-01: corroboration band. A NON-financial, NON-alt name the generic
    # sector model places 50–100% above market keeps a confident BUY/ACC only
    # when the engine's own evidence corroborates it: the computed (weighted)
    # methods must broadly agree (max/min ≤ 3×) AND the market-anchored sector-
    # P/E cross-check must itself clear the price. Otherwise the honest state is
    # LOW CONF. (The +60..+100% hole shipped 27 confident calls — AWL "BUY +91%"
    # with its own methods disagreeing 15×, REDINGTON +79% — before this gate.
    # Deep value CAN survive it: agreeing methods + a P/E leg above price.)
    if (verdict in ("BUY", "ACCUMULATE") and not alt
            and co.get("type") != "financial"
            and mos is not None and mos > 0.50):
        _comps = b.get("components") or []
        _wvals = [c.get("value") for c in _comps
                  if c.get("value") and c["value"] > 0 and (c.get("weight") or 0) > 0]
        _disp = (max(_wvals) / min(_wvals)) if len(_wvals) >= 2 else None
        # Tolerance 2.5×, calibrated on the audit's live sample: every name whose
        # upside the other legs actually corroborated sat at ≤2.3× dispersion
        # (COALINDIA 1.3, INFY 1.2, CESC 2.3), while the exit-multiple-inflated
        # calls read 2.6×+ (REDINGTON 2.6, AWL 3.7, SAMHI 6.0).
        _pe_leg = next((c.get("value") for c in _comps
                        if c.get("method") == "P/E (sector)"), None)
        _pe_ok = bool(_pe_leg and co.get("price") and _pe_leg >= co["price"])
        if _disp is None or _disp > 2.5 or not _pe_ok:
            verdict = "LOW CONF"
            reliable = False
            _why = []
            if _disp is not None and _disp > 3.0:
                _why.append(f"the engine's own methods disagree {_disp:.1f}×")
            if not _pe_ok:
                _why.append("the sector-P/E cross-check sits below the market price")
            reasons.append({"label": "Model", "score": 50,
                            "note": (f"Large upside ({mos*100:.0f}%) without corroboration — "
                                     + ("; ".join(_why) or "insufficient method evidence")
                                     + ". Not a confident call."),
                            "good": False, "bad": True})

    # ── FIX-13: conviction legs + the FM contract ────────────────────────────
    # Expose HOW MUCH to trust the call: method_dispersion (do the engine's own
    # methods agree?), sensitivity_swing (is the value robust to a ±1% assumption
    # move?), tv_share (how much rides on terminal value?), data_tier. Fold them
    # into conviction — a confident BUY requires agreeing methods, and confidence
    # is capped when the value is almost all TV or swings wildly. This is the
    # contract FIX-19's Fund Manager reads, so the field names/types are pinned.
    _disp = method_dispersion(b.get("components")) if not alt else None
    _tv = tv_share(v) if not alt else None
    _swing = sensitivity_swing(co, a, b.get("primary")) if (iv is not None and not alt) else None
    _tier = (conf.get("level") if isinstance(conf, dict) else None)
    _gate = "clean"

    # No confident BUY when the engine's own methods disagree > 2.5× (VAL-04).
    if verdict == "BUY" and _disp is not None and _disp > 2.5:
        verdict = "LOW CONF"
        reliable = False
        _gate = "high_dispersion"
        reasons.append({"label": "Conviction", "score": 45,
                        "note": f"Engine methods disagree {_disp:.1f}× (> 2.5×) — the fair "
                                "value isn't corroborated, so this is not a confident BUY.",
                        "good": False, "bad": True})
    # Speculative: almost all of the value is terminal value → cap confidence.
    if _tv is not None and _tv > 0.85 and isinstance(conf, dict) and conf.get("level") == "high":
        conf = {**conf, "level": "medium", "score": min(conf.get("score") or 0.79, 0.79)}
        _gate = "high_tv_share" if _gate == "clean" else _gate
        reasons.append({"label": "Conviction", "score": 55,
                        "note": f"{_tv*100:.0f}% of the fair value is terminal value — "
                                "confidence capped to MEDIUM.", "good": False, "bad": False})
    # Fragile: a ±1% assumption move swings the value > 80% → cap confidence.
    if _swing is not None and _swing > 0.80 and isinstance(conf, dict) and conf.get("level") == "high":
        conf = {**conf, "level": "medium", "score": min(conf.get("score") or 0.79, 0.79)}
        _gate = "high_sensitivity" if _gate == "clean" else _gate
        reasons.append({"label": "Conviction", "score": 55,
                        "note": f"A ±1% move in the discount rate / growth swings the value "
                                f"{_swing*100:.0f}% — confidence capped to MEDIUM.",
                        "good": False, "bad": False})
    if _gate == "clean" and verdict in ("LOW CONF", "NO DATA", "NO CALL"):
        _gate = "abstain"

    return {"valuation": v, "fundamentals": f, "technicals": t, "mos": mos,
            "intrinsic": iv, "confidence": conf, "reliable": reliable,
            "reasons": reasons, "composite": composite, "verdict": verdict,
            # Blended fair value breakdown — the per-method values + weights the
            # Valuation tab renders, plus the pure intrinsic-model value as one input.
            "blended": iv, "components": (alt["components"] if alt else b.get("components")),
            "dcf_value": b.get("primary"), "primary_method": b.get("primary_method"),
            "method": method, "alt_method": (alt["method"] if alt else None),
            "drivers": a.get("_drivers"), "valuation_sector": a.get("_valuation_sector"),
            # FIX-13 FM contract (pinned names/types): data_tier=str|None,
            # method_dispersion=float|None (max/min), sensitivity_swing=float|None
            # (fraction), tv_share=float|None (0-1), gate_state=str.
            "data_tier": _tier, "method_dispersion": _disp,
            "sensitivity_swing": _swing, "tv_share": _tv, "gate_state": _gate}
