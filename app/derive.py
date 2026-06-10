"""
app/derive.py — derive forward valuation drivers from a company's OWN history.

This replaces the previous approach where assumptions came from yfinance `info`
(stale third-party point estimates, a flat beta of ~1.0 for everyone). Here,
growth / margins / tax / reinvestment / ROE are computed deterministically from
the 7-year IndianAPI statements already stored in `historical_financials`, and
risk (beta), terminal growth and mature returns come from `sector_params`.

The result is an *independent*, reproducible, auditable assumption set: feed the
same history in and you get the same DCF out — no hidden vendor snapshot.

Public API:
    derive_assumptions(statements, valuation_sector, is_financial) -> dict
    where `statements` is { year:int -> { "PL":{...}, "BS":{...}, "CF":{...} } }
    (exactly the shape build_financials_response produces).

The returned dict is the full assumption block engines.py consumes, plus a
`_drivers` provenance sub-dict explaining where each number came from.
"""
from __future__ import annotations
from statistics import median

from . import sector_params as SP


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _series(statements, stmt, item):
    """Year-ascending list of (year, value) for a line item, skipping None."""
    out = []
    for yr in sorted(statements.keys()):
        v = (statements.get(yr, {}).get(stmt, {}) or {}).get(item)
        if v is not None:
            out.append((int(yr), float(v)))
    return out


def _cagr(series, max_n=4):
    """CAGR across the available window (capped at max_n years), positive ends only."""
    if len(series) < 2:
        return None
    pts = series[-(max_n + 1):]
    v0, v1 = pts[0][1], pts[-1][1]
    n = pts[-1][0] - pts[0][0]
    if v0 is None or v1 is None or v0 <= 0 or v1 <= 0 or n <= 0:
        return None
    return (v1 / v0) ** (1 / n) - 1


def _ratio_median(num_series, den_series, lo=None, hi=None, n=3):
    """Median of num/den over the last n overlapping years."""
    dn = dict(den_series)
    vals = []
    for yr, num in num_series[-n:]:
        den = dn.get(yr)
        if den and den != 0:
            vals.append(num / den)
    if not vals:
        return None
    m = median(vals)
    if lo is not None or hi is not None:
        m = _clamp(m, lo if lo is not None else -1e9, hi if hi is not None else 1e9)
    return m


def _networth_series(statements):
    """Net worth by year. Prefer the reported net_worth line; otherwise rebuild
    it as share capital + reserves. NEVER fall back to the bare `equity` line
    alone — that is face-value SHARE CAPITAL (₹100–500cr for a large company vs
    lakhs of crores of net worth), and using it silently inflated ROIC/ROE and
    deflated debt weights by 20–50×."""
    nw = _series(statements, "BS", "net_worth")
    if nw:
        return nw
    eq = dict(_series(statements, "BS", "equity"))
    rs = dict(_series(statements, "BS", "reserves"))
    out = [(y, (eq.get(y) or 0.0) + (rs.get(y) or 0.0))
           for y in sorted(set(eq) | set(rs))]
    return [(y, v) for y, v in out if v > 0]


def _company_roic(ebit_series, networth_series, borrowings_series, tax_rate):
    """Realised ROIC = NOPAT / invested capital, median over the overlapping
    recent years. Invested capital = net worth + borrowings (capital employed).
    Returns None when the inputs aren't available — caller falls back to sector.

    NOTE: this is deliberately conservative — net-cash firms (no borrowings) use
    net worth alone as the capital base, so a genuinely capital-light franchise
    reads a very high ROIC, which is then blended/capped by the caller."""
    nw = dict(networth_series)
    bw = dict(borrowings_series or [])
    vals = []
    for yr, ebit in ebit_series[-3:]:
        ic = (nw.get(yr) or 0) + (bw.get(yr) or 0)
        if ebit is not None and ic and ic > 0:
            vals.append(ebit * (1 - tax_rate) / ic)
    return median(vals) if vals else None


# ── Non-financial driver derivation (FCFF) ─────────────────────────────────
def _derive_nonfinancial(statements, vs):
    p = SP.params(vs)
    drivers = {}

    rev = _series(statements, "PL", "revenue")
    ebit = _series(statements, "PL", "ebit")
    ebitda = _series(statements, "PL", "ebitda")
    pat = _series(statements, "PL", "pat")
    tax = _series(statements, "PL", "tax")
    pbt = _series(statements, "PL", "pbt")
    borrowings = _series(statements, "BS", "borrowings")
    networth = _networth_series(statements)
    interest = _series(statements, "PL", "interest_expense")

    # Commodity cyclicals (metals, oil & gas, coal) trade off MID-CYCLE earnings,
    # not the trailing peak. Capitalising peak margins/growth into perpetuity is
    # exactly why a DCF over-values them (ONGC/Coal India looked +100%). For these
    # sectors we (a) normalise the margin over a longer 5y window and (b) cap
    # forward growth near long-run nominal GDP rather than the cycle's recent CAGR.
    cyclical = vs in ("METAL", "ENERGY")
    # Semi-cyclicals (autos, cement) aren't commodity businesses, but their
    # recent growth IS the cycle: an auto OEM printing 18% off an up-cycle
    # cannot compound that for years (capitalising M&M's SUV boom produced a
    # +114% MoS — an obvious peak-cycle artifact). Normalise them to a
    # mid-cycle cap between the commodity cap and the secular-growth cap.
    semi_cyclical = vs in ("AUTO", "CEMENT")

    # Near-term revenue growth: blend 3–4yr CAGR with the latest YoY, then cap.
    cagr = _cagr(rev, max_n=4)
    yoy = None
    if len(rev) >= 2 and rev[-2][1] > 0:
        yoy = rev[-1][1] / rev[-2][1] - 1
    cand = [g for g in (cagr, yoy) if g is not None]
    if cand:
        rev_growth = sum(cand) / len(cand)
    else:
        rev_growth = p["terminal_growth"] + 0.03
    # commodities: no secular high growth; semi-cyclicals: mid-cycle only
    growth_hi = 0.08 if cyclical else 0.12 if semi_cyclical else 0.18
    rev_growth = _clamp(rev_growth, 0.02, growth_hi)
    drivers["rev_growth"] = (f"blend(CAGR={_pct(cagr)}, YoY={_pct(yoy)}) "
                             f"capped{' (cyclical)' if cyclical else ' (mid-cycle)' if semi_cyclical else ''}")

    # EBIT margin: median over 5y for cyclicals (through-cycle), 3y otherwise.
    margin_n = 5 if cyclical else 3
    ebit_margin = _ratio_median(ebit, rev, lo=0.02, hi=0.45, n=margin_n)
    if ebit_margin is None:
        em = _ratio_median(ebitda, rev, lo=0.04, hi=0.55, n=margin_n)
        ebit_margin = (em * 0.8) if em else 0.14
    drivers["ebit_margin"] = (f"median(EBIT/Rev, {margin_n}y)" if ebit
                              else f"0.8×median(EBITDA/Rev, {margin_n}y)")

    # Effective tax rate from PBT (clamp to India statutory band).
    tax_rate = _ratio_median(tax, pbt, lo=0.12, hi=0.32, n=3) or 0.25
    drivers["tax_rate"] = "median(Tax/PBT, 3y)"

    # Reinvestment tied to growth and ROIC (Damodaran identity):
    #   reinvestment_rate = g / ROIC. Keeps FCFF internally consistent.
    #
    # CRITICAL: use the company's OWN realised ROIC for the EXPLICIT period, not
    # the flat sector ROIC. Capital-light compounders (Nestlé, Asian Paints,
    # Titan, Bajaj Auto) earn 40–100%+ ROIC, so they fund mid-single-digit growth
    # out of a SMALL slice of profit — most of NOPAT is free cash. Using the
    # sector's ~22% ROIC forced them to "reinvest" ~40% of profit they don't
    # actually need to, understating their FCFF (and fair value) by ~30% and
    # printing a false AVOID on the whole quality cohort. We blend the firm's
    # ROIC toward the sector (don't fully trust a single spot read) and clamp to
    # [sector_ROIC, 0.50] so the fix only LIFTS genuine high-return franchises and
    # never fabricates cash for ordinary or sub-par ones. The TERMINAL value still
    # fades to the sector mature ROIC in engines.py — high returns now, competed
    # away in perpetuity.
    company_roic = _company_roic(ebit, networth, borrowings, tax_rate)
    base_roic = company_roic if company_roic else p["mature_roic"]
    roic_used = _clamp(0.6 * base_roic + 0.4 * p["mature_roic"], p["mature_roic"], 0.50)
    reinvest_rate = _clamp(rev_growth / roic_used, 0.10, 0.80) if roic_used else 0.40
    drivers["reinvest_rate"] = (f"g/ROIC (g={_pct(rev_growth)}, "
                                f"ROIC={_pct(roic_used)} — own {_pct(company_roic)} "
                                f"blended to sector {_pct(p['mature_roic'])})")

    # Capital structure from the latest balance sheet.
    debt_weight = 0.15
    if borrowings and networth:
        b = borrowings[-1][1]
        e = networth[-1][1]
        if b is not None and e and (b + e) > 0:
            debt_weight = _clamp(b / (b + e), 0.0, 0.60)
    drivers["debt_weight"] = "borrowings/(borrowings+net worth), latest"

    cost_debt = 0.085
    if interest and borrowings:
        cd = _ratio_median(interest, borrowings, lo=0.06, hi=0.12, n=2)
        if cd:
            cost_debt = cd

    # ── Competitive-advantage period (CAP) — quality-dependent fade horizon ──
    # A flat 8-year horizon priced Nestlé and Tata Steel as if their excess
    # returns die at the same speed. They don't: empirically, high-ROIC moats
    # (brands, distribution, switching costs) defend excess returns for well
    # over a decade, which is precisely what justifies their premium multiples.
    # So the horizon itself is earned from the data: businesses with durable,
    # well-above-sector ROIC and real growth get more franchise years; ordinary
    # businesses keep 8; commodity cyclicals NEVER get an extended runway (their
    # "moat" is the cycle).
    fade_years = 8
    if not cyclical:
        roic_q = roic_used / p["mature_roic"] if p.get("mature_roic") else 1.0
        if roic_q >= 1.5 and rev_growth >= 0.08:
            fade_years = 14
        elif roic_q >= 1.2 or rev_growth >= 0.12:
            fade_years = 11
    drivers["fade_years"] = (f"CAP {fade_years}y (ROIC {_pct(roic_used)} vs sector "
                             f"{_pct(p['mature_roic'])}{', cyclical-capped' if cyclical else ''})")

    return {
        "beta": p["beta"], "risk_free": SP.RISK_FREE, "erp": SP.ERP,
        "rev_growth": round(rev_growth, 4),
        "ebit_margin": round(ebit_margin, 4),
        "tax_rate": round(tax_rate, 4),
        "reinvest_rate": round(reinvest_rate, 4),
        "debt_weight": round(debt_weight, 4),
        "cost_debt": round(cost_debt, 4),
        "fade_years": fade_years,
        "terminal_growth": p["terminal_growth"],
        # RI fields unused for non-financials but kept for a uniform dict:
        "forecast_roe": 0.15, "terminal_roe": p["mature_roe"], "payout": 0.25,
        "_drivers": drivers, "_valuation_sector": vs,
    }


# ── Financial driver derivation (Residual Income) ──────────────────────────
def _derive_financial(statements, vs):
    p = SP.params(vs)
    drivers = {}

    pat = _series(statements, "PL", "pat")
    # CRITICAL: ROE must use NET WORTH, never share capital. Fall back to
    # share capital + reserves (≈ net worth), not reserves alone.
    networth = _networth_series(statements)
    dividends = _series(statements, "CF", "dividends")

    # 3-year median ROE — the franchise's RECENT, post-cleanup earning power.
    # A 5y window anchored banks to the 2019-21 NPA-cycle / COVID trough, which
    # are no longer representative; the last 3y reflect normalized returns and is
    # what should drive the high-ROE phase of the two-stage RI model.
    forecast_roe = _ratio_median(pat, networth, lo=0.06, hi=0.30, n=3)
    if forecast_roe is None:
        forecast_roe = p["mature_roe"]
    drivers["forecast_roe"] = "median(PAT/NetWorth, 3y)"

    # Fade realized ROE toward the sector's mature ROE, but weight the franchise's
    # OWN realized return more (0.55) — India's best private banks/NBFCs sustain
    # structurally high ROE, and over-fading them to a generic sector mean made
    # the model too bearish on quality financials (ICICI/Axis looked ~20-25% rich).
    terminal_roe = _clamp(0.55 * forecast_roe + 0.45 * p["mature_roe"], 0.10, 0.22)
    drivers["terminal_roe"] = "0.55×realized + 0.45×sector mature ROE"

    # Payout from dividends/PAT (dividends stored negative in CF → abs).
    payout = 0.20
    if dividends and pat:
        dp = dict(pat)
        vals = []
        for yr, dv in dividends[-3:]:
            pp = dp.get(yr)
            if pp and pp > 0:
                vals.append(min(abs(dv) / pp, 0.95))
        if vals:
            payout = _clamp(median(vals), 0.0, 0.70)
    drivers["payout"] = "median(|Dividends|/PAT, 3y)"

    # ── Competitive-advantage period for financials ──────────────────────────
    # A compounding franchise (high ROE retained and redeployed at high ROE —
    # Bajaj Finance, the best private banks) defends excess returns far longer
    # than 8 years; a sub-Ke lender does not deserve extra years (and with
    # ROE < Ke a longer runway would rightly SUBTRACT value, so the quality
    # gate keeps the extension one-sided).
    fade_years = 8
    if forecast_roe >= 0.15 and payout <= 0.35:
        fade_years = 12
    elif forecast_roe >= 0.13:
        fade_years = 10
    drivers["fade_years"] = (f"CAP {fade_years}y (ROE {_pct(forecast_roe)}, "
                             f"payout {_pct(payout)})")

    return {
        "beta": p["beta"], "risk_free": SP.RISK_FREE, "erp": SP.ERP,
        "forecast_roe": round(forecast_roe, 4),
        "terminal_roe": round(terminal_roe, 4),
        "payout": round(payout, 4),
        "fade_years": fade_years,
        "terminal_growth": p["terminal_growth"],
        # FCFF fields unused for financials but kept for a uniform dict:
        "rev_growth": 0.10, "ebit_margin": 0.12, "tax_rate": 0.25,
        "reinvest_rate": 0.35, "debt_weight": 0.20, "cost_debt": 0.085,
        "_drivers": drivers, "_valuation_sector": vs,
    }


def derive_assumptions(statements: dict, valuation_sector: str,
                       is_financial: bool) -> dict:
    """Derive the full assumption block from stored history + sector params."""
    statements = statements or {}
    if is_financial:
        return _derive_financial(statements, valuation_sector)
    return _derive_nonfinancial(statements, valuation_sector)


def _pct(x):
    return f"{x*100:.1f}%" if isinstance(x, (int, float)) else "n/a"
