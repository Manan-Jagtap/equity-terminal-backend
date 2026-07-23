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


def _company_roic(ebit_series, networth_series, borrowings_series, tax_rate,
                  cash_series=None, goodwill_series=None):
    """Realised ROIC = NOPAT / OPERATING invested capital, median over the recent
    overlapping years. Invested capital = net worth + borrowings − cash &
    investments − goodwill/intangibles.

    Netting out the cash pile and acquisition goodwill is the fix for the biggest
    understatement in the model: cash-rich compounders (Maruti reads 11.6% on the
    gross base, ~25-30% operating) and acquisition-heavy staples (HUL, Asian
    Paints) were dragged to a LOW ROIC by a bloated capital base, which pinned
    `roic_used` to the sector floor and forced reinvestment to 0.75-0.80 — so the
    DCF kept only ~20-25% of NOPAT as free cash and printed false AVOIDs across
    the quality cohort. Operating ROIC is also the right base for the
    reinvestment identity (how much capital INCREMENTAL growth actually needs).
    Downstream clamps keep the resulting ROIC in a sane band."""
    nw = dict(networth_series)
    bw = dict(borrowings_series or [])
    cs = dict(cash_series or [])
    gw = dict(goodwill_series or [])
    vals = []
    for yr, ebit in ebit_series[-3:]:
        ic = ((nw.get(yr) or 0) + (bw.get(yr) or 0)
              - (cs.get(yr) or 0) - (gw.get(yr) or 0))
        if ebit is not None and ic and ic > 0:
            vals.append(ebit * (1 - tax_rate) / ic)
    return median(vals) if vals else None


def _actual_reinvestment(capex, dep, receivables, inventory, payables, ebit, tax_rate):
    """The company's MEASURED net-reinvestment rate = (|capex| − D&A + ΔNWC) /
    NOPAT, median over the overlapping years — the institutional FCFF build read
    straight off the statements. ΔNWC = Δ(receivables + inventory − payables).
    None when we can't measure it from ≥2 clean years; each year is clamped to
    tame lumpy-capex / one-off outliers."""
    cap, d, e = dict(capex or []), dict(dep or []), dict(ebit or [])
    nwc = {}
    for src, sign in ((receivables, 1), (inventory, 1), (payables, -1)):
        for y, v in (src or []):
            if v is not None:
                nwc[y] = nwc.get(y, 0.0) + sign * v
    nwc_years = sorted(nwc)
    dnwc = {nwc_years[i]: nwc[nwc_years[i]] - nwc[nwc_years[i - 1]]
            for i in range(1, len(nwc_years))}
    rates = []
    for y in sorted(set(cap) & set(e)):
        nopat = (e.get(y) or 0) * (1 - tax_rate)
        if nopat <= 0:
            continue
        net_reinv = abs(cap.get(y) or 0) - (d.get(y) or 0) + dnwc.get(y, 0.0)
        rates.append(_clamp(net_reinv / nopat, -0.30, 1.20))
    return median(rates) if len(rates) >= 2 else None


# ── Non-financial driver derivation (FCFF) ─────────────────────────────────
def _derive_nonfinancial(statements, vs):
    p = SP.params(vs)
    drivers = {}

    rev = _series(statements, "PL", "revenue")
    ebit = _series(statements, "PL", "ebit")
    ebitda = _series(statements, "PL", "ebitda")
    dep = _series(statements, "PL", "depreciation")
    pat = _series(statements, "PL", "pat")
    tax = _series(statements, "PL", "tax")
    pbt = _series(statements, "PL", "pbt")
    borrowings = _series(statements, "BS", "borrowings")
    networth = _networth_series(statements)
    interest = _series(statements, "PL", "interest_expense")
    # excess cash + treasury investments + goodwill to net out of the ROIC base.
    # Cash-rich industrials (Maruti, Bajaj Auto) park most of their treasury in
    # the non-current `investments` line, not `cash` — so netting only cash left
    # their capital base bloated and ROIC understated. Net the full treasury.
    _cash = {}
    for _line in ("cash", "cash_equivalents", "short_term_investments", "investments"):
        for _yr, _v in _series(statements, "BS", _line):
            _cash[_yr] = _cash.get(_yr, 0.0) + _v
    cash_series = sorted(_cash.items())
    goodwill_series = _series(statements, "BS", "goodwill")
    # For the ACTUAL net-reinvestment cross-check (capex − D&A + ΔNWC): capex from
    # the cash flow, working-capital lines from the balance sheet.
    capex = _series(statements, "CF", "capex")
    receivables = _series(statements, "BS", "receivables")
    inventory = _series(statements, "BS", "inventory")
    payables = _series(statements, "BS", "payables")

    # Commodity cyclicals (metals, oil & gas, coal) trade off MID-CYCLE earnings,
    # not the trailing peak. Capitalising peak margins/growth into perpetuity is
    # exactly why a DCF over-values them (ONGC/Coal India looked +100%). For these
    # sectors we (a) normalise the margin over a longer 5y window and (b) cap
    # forward growth near long-run nominal GDP rather than the cycle's recent CAGR.
    cyclical = vs in ("METAL", "ENERGY", "PAPER", "SUGAR", "TEXTILES")
    # Semi-cyclicals (autos, cement) aren't commodity businesses, but their
    # recent growth IS the cycle: an auto OEM printing 18% off an up-cycle
    # cannot compound that for years (capitalising M&M's SUV boom produced a
    # +114% MoS — an obvious peak-cycle artifact). Normalise them to a
    # mid-cycle cap between the commodity cap and the secular-growth cap.
    # Aviation is fuel-cyclical: a boom-year margin can't be capitalised, and its
    # 3-yr window (post-COVID recovery) is a poor normal — treat like a semi-cyclic.
    semi_cyclical = vs in ("AUTO", "CEMENT", "AVIATION")
    # CHEMICALS and CEMENT need THROUGH-CYCLE margins too: chem's 3-yr window is
    # the FY23-26 specialty down-cycle, and cement's reported EBIT is distorted by
    # new-capacity depreciation (EBIT ≈ half of normalized). Normalise over 5y.
    # Aviation too — fuel swings make any single 3-yr margin unrepresentative.
    # Cables: copper-cost swings distort a single year's margin → smooth over 5y.
    # Construction: lumpy project execution makes a single year unrepresentative.
    through_cycle = cyclical or vs in ("CHEMICALS", "CEMENT", "AVIATION", "CABLES", "CONSTRUCTION")

    # Near-term revenue growth: weight the stable multi-year CAGR over a single
    # (possibly trough or peak) YoY, then cap. A 50/50 blend let one soft quarter
    # (PI −16%, Colgate −) set a ~2% perpetual growth on structural compounders.
    cagr = _cagr(rev, max_n=4)
    yoy = None
    if len(rev) >= 2 and rev[-2][1] > 0:
        yoy = rev[-1][1] / rev[-2][1] - 1
    if cagr is not None and yoy is not None:
        rev_growth = 0.65 * cagr + 0.35 * yoy
    elif cagr is not None:
        rev_growth = cagr
    elif yoy is not None:
        rev_growth = yoy
    else:
        rev_growth = p["terminal_growth"] + 0.03
    # commodities: no secular high growth; semi-cyclicals: mid-cycle only
    # DISTRIBUTION joins the 12% tier (DAT-02): pass-through toplines can grow
    # fast, but capitalising that at full-franchise growth is how a thin-margin
    # distributor gets valued like a compounder.
    growth_hi = 0.08 if cyclical else 0.12 if (semi_cyclical or vs == "DISTRIBUTION") else 0.18
    rev_growth = _clamp(rev_growth, 0.02, growth_hi)
    drivers["rev_growth"] = (f"0.65·CAGR({_pct(cagr)})+0.35·YoY({_pct(yoy)}) "
                             f"capped{' (cyclical)' if cyclical else ' (mid-cycle)' if semi_cyclical else ''}")

    # EBIT margin: median over 5y through-cycle (cyclicals, chem, cement), 3y else.
    margin_n = 5 if through_cycle else 3
    ebit_margin = _ratio_median(ebit, rev, lo=0.02, hi=0.45, n=margin_n)
    # CEMENT: reported EBIT is depreciation-distorted by the capex cycle (EBIT ≈
    # half of normalized while EBITDA is steady). Rebuild EBIT from the stable
    # EBITDA margin less a normalized D&A rate.
    if vs == "CEMENT":
        em = _ratio_median(ebitda, rev, lo=0.08, hi=0.45, n=5)
        dm = _ratio_median(dep, rev, lo=0.0, hi=0.15, n=5)
        if em is not None:
            ebit_margin = _clamp(em - (dm if dm is not None else 0.07), 0.02, 0.45)
            drivers["ebit_margin"] = f"EBITDA/Rev(5y {_pct(em)}) − D&A/Rev({_pct(dm)})"
    if ebit_margin is None:
        em = _ratio_median(ebitda, rev, lo=0.04, hi=0.55, n=margin_n)
        ebit_margin = (em * 0.8) if em else 0.14
    if "ebit_margin" not in drivers:
        drivers["ebit_margin"] = (f"median(EBIT/Rev, {margin_n}y)" if ebit
                                  else f"0.8×median(EBITDA/Rev, {margin_n}y)")

    # Terminal margin — margins mean-revert. Glide the near-term margin toward the
    # company's OWN through-cycle level (up-to-7y median), but only ~40% of the way
    # and bounded ±3.5pp, so an elevated margin fades a little and a depressed one
    # recovers a little, without a large calibration swing. Cyclicals already start
    # on a through-cycle margin, so their glide is ≈flat. engines.py does the glide.
    terminal_ebit_margin = ebit_margin
    _longrun = _ratio_median(ebit, rev, lo=0.02, hi=0.45, n=7) if ebit else None
    if _longrun is not None and ebit_margin is not None:
        _target = ebit_margin + 0.4 * (_longrun - ebit_margin)
        terminal_ebit_margin = _clamp(_target, ebit_margin - 0.035, ebit_margin + 0.035)
        if abs(terminal_ebit_margin - ebit_margin) >= 0.003:
            drivers["ebit_margin"] += (
                f" → glides to {_pct(terminal_ebit_margin)} (mean-revert to "
                f"through-cycle {_pct(_longrun)})")

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
    company_roic = _company_roic(ebit, networth, borrowings, tax_rate,
                                  cash_series, goodwill_series)
    base_roic = company_roic if company_roic else p["mature_roic"]
    roic_used = _clamp(0.6 * base_roic + 0.4 * p["mature_roic"], p["mature_roic"], 0.50)
    # High-quality slowdown floor: a DURABLE high-ROIC franchise (HUL, Colgate,
    # Dabur, Glaxo) in a temporary demand dip shouldn't have one trough year set a
    # permanent ~5% growth. Floor its near-term growth at a franchise-normal 8%.
    if not cyclical and roic_used >= 1.2 * p["mature_roic"] and rev_growth < 0.08:
        rev_growth = 0.08
        drivers["rev_growth"] += " → floored 8% (durable high-ROIC franchise)"
    # Ceiling 0.65 (was 0.80): the g/ROIC identity with a cyclically DEPRESSED
    # spot ROIC (capital-intensive names mid-build-out read ~sector ROIC) demanded
    # a ~80% reinvestment that these businesses don't actually run — UltraTech,
    # Grasim, Ambuja generate real FCF and pay dividends. 0.65 is still heavy
    # reinvestment; it lifts the maxed-out names without fabricating cash for
    # ordinary ones (whose roic_used is high enough that they never hit the cap).
    identity = rev_growth / roic_used if roic_used else 0.40
    # Cross-check the g/ROIC identity against the company's MEASURED net-capex
    # intensity. We use it ONLY to TEMPER (raise reinvestment → lower FCFF) when a
    # name actually plows back materially more than the identity implies — a
    # capital-heavy build-out the identity would under-reinvest and thus over-value.
    # We deliberately do NOT lower reinvestment for asset-light names off a near-
    # zero actual capex: the identity already credits their high ROIC, and treating
    # growth as free would inflate the quality cohort. One-directional = safe.
    actual_reinv = _actual_reinvestment(capex, dep, receivables, inventory,
                                        payables, ebit, tax_rate)
    if actual_reinv is not None and actual_reinv > identity + 0.05:
        reinvest_rate = _clamp(0.5 * identity + 0.5 * actual_reinv, 0.10, 0.75)
        drivers["reinvest_rate"] = (
            f"g/ROIC {_pct(identity)} raised toward measured net-capex intensity "
            f"{_pct(actual_reinv)} (capital-heavy build-out)")
    else:
        reinvest_rate = _clamp(identity, 0.10, 0.65)
        drivers["reinvest_rate"] = (f"g/ROIC (g={_pct(rev_growth)}, "
                                    f"ROIC={_pct(roic_used)} — own {_pct(company_roic)} "
                                    f"blended to sector {_pct(p['mature_roic'])})"
                                    + (f"; actual net-capex {_pct(actual_reinv)}"
                                       if actual_reinv is not None else ""))

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
    # The runway is earned from QUALITY (ROIC durability), NOT current growth —
    # a high-ROE staple in a temporary slowdown is the most durable moat, yet the
    # old growth gate handed it the SHORTEST 8y horizon. Base the CAP on roic_q.
    fade_years = 8
    if not cyclical:
        roic_q = roic_used / p["mature_roic"] if p.get("mature_roic") else 1.0
        if roic_q >= 1.5:
            fade_years = 15
        elif roic_q >= 1.25:
            fade_years = 12
        elif roic_q >= 1.1:
            fade_years = 10
        elif vs in ("CEMENT", "AUTO", "CHEMICALS"):
            # capital-intensive franchises whose reported ROIC ≈ sector (dragged
            # by under-utilised recent capacity) still have durable positions
            # (limestone reserves, distribution, switching costs) worth >8y.
            fade_years = 10
    drivers["fade_years"] = (f"CAP {fade_years}y (ROIC {_pct(roic_used)} vs sector "
                             f"{_pct(p['mature_roic'])}{', cyclical-capped' if cyclical else ''})")

    # ── VAL-02 (nonfin analog of FIX-17): evidence-earned compounder terminal
    # ROIC. The terminal value fades EVERY business to the sector's mature ROIC,
    # which prices DMART/HUL-class franchises as if their return advantage dies
    # completely in perpetuity — the residual compounder level-bias both the
    # valuation audit and groundtruth cross-check flagged. A franchise that has
    # PROVEN a durable, well-above-sector operating ROIC earns a terminal that
    # fades only HALFWAY to sector. Strictly gated + one-sided + bounded:
    #   · non-cyclical only (a cyclical's "moat" is the cycle),
    #   · roic_q ≥ 1.4 (well above the 1.25 fade tier — clearly earned),
    #   · ≥6y of statements (durability, not one good print),
    #   · terminal margin not in structural decline (≥0.9× current),
    #   · lift capped at 1.5× sector mature ROIC and 0.40 absolute,
    #   · never set below the sector default (one-sided by construction).
    terminal_roic = None
    if (not cyclical and len(ebit) >= 6
            and p.get("mature_roic") and roic_used >= 1.4 * p["mature_roic"]
            and terminal_ebit_margin >= 0.9 * ebit_margin):
        cand = min(0.5 * roic_used + 0.5 * p["mature_roic"],
                   1.5 * p["mature_roic"], 0.40)
        if cand > p["mature_roic"]:
            terminal_roic = round(cand, 4)
            drivers["terminal_roic"] = (
                f"earned compounder terminal {_pct(terminal_roic)} — fades halfway "
                f"to sector {_pct(p['mature_roic'])} (own {_pct(roic_used)} ≥1.4×, "
                f"{len(ebit)}y history, stable margins); capped 1.5×sector / 40%")

    return {
        "beta": p["beta"], "risk_free": SP.RISK_FREE, "erp": SP.ERP,
        "rev_growth": round(rev_growth, 4),
        "ebit_margin": round(ebit_margin, 4),
        "terminal_ebit_margin": round(terminal_ebit_margin, 4),
        "tax_rate": round(tax_rate, 4),
        "reinvest_rate": round(reinvest_rate, 4),
        "debt_weight": round(debt_weight, 4),
        "cost_debt": round(cost_debt, 4),
        "fade_years": fade_years,
        "terminal_growth": p["terminal_growth"],
        "terminal_roic": terminal_roic,   # VAL-02: earned compounder terminal (None = sector default)
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
    # Ground the forecast in the LATEST realized ROE. A lender whose ROE has
    # DECLINED (LIC HF: 3y median ~15% but now ~13.5%) must not be valued on its
    # better past — cap the forecast at a small premium to the most recent year.
    _latest_roe = None
    try:
        if pat and networth and pat[-1][0] == networth[-1][0] and networth[-1][1]:
            _latest_roe = pat[-1][1] / networth[-1][1]
    except Exception:
        _latest_roe = None
    if _latest_roe is not None and 0.03 < _latest_roe < 0.35 and forecast_roe > _latest_roe * 1.10:
        forecast_roe = _latest_roe * 1.10
    drivers["forecast_roe"] = "median(PAT/NetWorth, 3y), capped near latest realized"

    # Fade realized ROE toward the sector's mature ROE, but weight the franchise's
    # OWN realized return more (0.55) — India's best private banks/NBFCs sustain
    # structurally high ROE, and over-fading them to a generic sector mean made
    # the model too bearish on quality financials (ICICI/Axis looked ~20-25% rich).
    # Trust the franchise's OWN realized ROE more (0.70) and raise the ceiling to
    # 0.26. The old 0.55/0.45 with a 0.22 clamp created a hard terminal-P/B ceiling
    # of ~2.5x — the engine literally could not value ANY lender above ~2.5x book,
    # so Bajaj Finance (5.7x), Chola (4.9x), SBI Card all printed a false AVOID.
    # Weak lenders (forecast_roe ~0.05-0.06) are untouched by the higher ceiling.
    # A STRONG franchise (forecast above the sector mature ROE) reverts DOWN toward
    # it; a below-average lender must NEVER be modelled improving UP to the sector's
    # best — that was the LIC-HF bug (forecast 15% → terminal pulled to 16% via the
    # 0.30×18% NBFC mature term, inflating a fake +138% BUY). Cap terminal at the
    # forecast so the blend can only fade excess returns down, never manufacture them.
    terminal_roe = _clamp(min(forecast_roe, 0.70 * forecast_roe + 0.30 * p["mature_roe"]),
                          0.08, 0.26)
    drivers["terminal_roe"] = "min(forecast, 0.70×realized + 0.30×sector mature)"

    # FIX-17: evidence-earned compounder terminal ROE (VAL-02 / VAL-09). The
    # terminal-at-forecast cap above (correctly) stops a WEAK lender inflating a
    # fake terminal, but it also CLIPS a proven compounder whose forecast is
    # temporarily depressed (HDFC Bank post-merger: 5y ROE 15-16% → recent ~13%).
    # When the franchise has demonstrated a DURABLE excess return over its history
    # — median ROE ≥ 1.2×Ke (excess return relative to ITS OWN risk, so a higher-
    # beta HFC like LIC HF faces a higher bar and stays capped) — let the terminal
    # fade toward the level the history PROVES (the upper-half median, so a single
    # trough year doesn't define the durable state), capped 18%. Earned & ONE-
    # SIDED: never lowers the terminal, and no ≥1.2×Ke proof → no lift at all.
    _roe_hist = []
    if pat and networth:
        _nw = dict(networth)
        for _yr, _pv in pat:
            _nv = _nw.get(_yr)
            if _nv and _nv > 0 and _pv is not None:
                _roe_hist.append(_pv / _nv)
    if len(_roe_hist) >= 4:
        _ke = SP.RISK_FREE + p["beta"] * SP.ERP
        _franchise0 = median(sorted(_roe_hist)[len(_roe_hist) // 2:])
        # Lift ONLY a STABLE franchise in a mild dip — latest realized ROE within
        # 20% of its proven level. HDFC Bank (latest 0.130 vs franchise 0.154,
        # ratio 0.84) is a durable ~15% franchise temporarily depressed. A
        # boom-bust name whose "franchise" is an unrepeatable PEAK (Motilal 0.145
        # vs 0.231 → 0.63; SBI Card 0.138 vs 0.208 → 0.66) is EXCLUDED — its
        # upper-half median is a peak, not a level it will revert to, and lifting
        # it would wrongly push a banded call into the divergence gate.
        _stable = (_latest_roe is not None and _franchise0 > 0
                   and _latest_roe >= 0.80 * _franchise0)
        if median(_roe_hist) >= 1.2 * _ke and _stable:
            _franchise = _franchise0
            # BOUNDED recovery: a TEMPORARILY depressed franchise reverts a modest
            # step (≤ +3 pts) toward its proven level — never a windfall. This is
            # what separates HDFC Bank (franchise 0.154 vs forecast 0.136, a small
            # gap → near-full lift, lands in band) from a structurally-low name
            # (Manappuram forecast 0.068 vs 0.159 → capped to +0.03, a modest
            # 0.098, not an unearned 2.3× jump).
            _earned = _clamp(min(_franchise, forecast_roe + 0.03, 0.18), 0.08, 0.26)
            if _earned > terminal_roe:
                terminal_roe = _earned
                drivers["terminal_roe"] = (
                    f"evidence-earned franchise ROE {_pct(terminal_roe)} — proven ≥1.2×Ke "
                    "persistence; terminal reverts to the demonstrated franchise, not the "
                    "depressed forecast (FIX-17)")

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
    if forecast_roe >= 0.18 and payout <= 0.30:
        fade_years = 15          # elite compounder (Bajaj/Chola-class) holds longer
    elif forecast_roe >= 0.15 and payout <= 0.35:
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
