"""
app/forensics.py — accounting-quality / forensic red-flags from stored history.

Every signal is computed deterministically from the company's OWN 5-year
statements (the same `statements` dict derive.py consumes), so it's transparent
and reproducible. We compute what the ingested data supports TODAY:

  · Piotroski F-score (adapted to the tests our data supports)
  · Sloan accrual ratio (earnings backed by cash, or by accruals?)
  · Cash conversion (CFO / PAT)
  · Interest coverage (EBIT / interest)
  · Net debt / EBITDA
  · Leverage trend (D/E now vs 3y ago)

Each metric carries a red/amber/green flag; the module also returns an overall
accounting-quality composite (0-100) and a plain-language flag list. The classic
Beneish M-score and full Altman Z'' need receivables / working-capital / SG&A
that aren't ingested yet — they're listed under `pending` and wired in once the
ingester captures those lines.

Safe by construction: any missing input yields None / a skipped test, never a
crash. Financials (banks/NBFC/insurers) get a reduced set — coverage/accrual
mechanics don't map cleanly to a lender's P&L — and are flagged as limited.
"""
from __future__ import annotations


def _v(statements, year, stmt, item):
    return ((statements.get(year, {}) or {}).get(stmt, {}) or {}).get(item)


def _num(x):
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _flag(value, good, amber, higher_is_better=True):
    """Return 'green'/'amber'/'red' for a value against two thresholds."""
    if value is None:
        return None
    if higher_is_better:
        if value >= good:  return "green"
        if value >= amber: return "amber"
        return "red"
    else:  # lower is better
        if value <= good:  return "green"
        if value <= amber: return "amber"
        return "red"


def _score_from_flag(flag):
    return {"green": 90.0, "amber": 60.0, "red": 25.0}.get(flag)


def _piotroski(statements, years):
    """Adapted Piotroski F-score. Two of the classic nine tests (current ratio,
    share issuance) need current-assets/share-history we don't store, so we run
    the seven the data supports and report the score out of those."""
    if len(years) < 2:
        return None
    y, p = years[-1], years[-2]
    g = lambda yr, s, i: _num(_v(statements, yr, s, i))
    tests = []

    pat_y, ta_y, ta_p = g(y, "PL", "pat"), g(y, "BS", "total_assets"), g(p, "BS", "total_assets")
    pat_p = g(p, "PL", "pat")
    cfo_y = g(y, "CF", "operating_cf")
    roa_y = (pat_y / ta_y) if (pat_y is not None and ta_y) else None
    roa_p = (pat_p / ta_p) if (pat_p is not None and ta_p) else None

    def add(name, cond, note):
        if cond is None:
            return
        tests.append({"name": name, "pass": bool(cond), "note": note})

    add("ROA positive", (roa_y > 0) if roa_y is not None else None,
        f"ROA {roa_y*100:.1f}%" if roa_y is not None else "")
    add("Operating cash flow positive", (cfo_y > 0) if cfo_y is not None else None,
        "CFO > 0" if cfo_y is not None else "")
    add("ROA improving", (roa_y > roa_p) if (roa_y is not None and roa_p is not None) else None,
        "ΔROA > 0")
    add("Cash > accruals (CFO > PAT)", (cfo_y > pat_y) if (cfo_y is not None and pat_y is not None) else None,
        "earnings backed by cash")

    ltd_y, ltd_p = g(y, "BS", "lt_debt"), g(p, "BS", "lt_debt")
    lev_y = (ltd_y / ta_y) if (ltd_y is not None and ta_y) else None
    lev_p = (ltd_p / ta_p) if (ltd_p is not None and ta_p) else None
    add("Leverage falling", (lev_y < lev_p) if (lev_y is not None and lev_p is not None) else None,
        "long-term debt / assets ↓")

    rev_y, rev_p = g(y, "PL", "revenue"), g(p, "PL", "revenue")
    gp_y, gp_p = g(y, "PL", "gross_profit"), g(p, "PL", "gross_profit")
    gm_y = (gp_y / rev_y) if (gp_y is not None and rev_y) else None
    gm_p = (gp_p / rev_p) if (gp_p is not None and rev_p) else None
    add("Gross margin improving", (gm_y > gm_p) if (gm_y is not None and gm_p is not None) else None,
        "Δgross margin > 0")

    at_y = (rev_y / ta_y) if (rev_y is not None and ta_y) else None
    at_p = (rev_p / ta_p) if (rev_p is not None and ta_p) else None
    add("Asset turnover improving", (at_y > at_p) if (at_y is not None and at_p is not None) else None,
        "Δ(revenue/assets) > 0")

    if not tests:
        return None
    score = sum(1 for t in tests if t["pass"])
    return {"score": score, "max": len(tests), "tests": tests,
            "normalized": round(score / len(tests) * 9, 1)}  # on the familiar 0-9 scale


def _avg(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def _ca(bs):
    """Total current assets — prefer the reported aggregate, else reconstruct
    from components (last-resort; the aggregate is normally present)."""
    bs = bs or {}
    if bs.get("current_assets") is not None:
        return _num(bs["current_assets"])
    parts = [_num(bs.get(k)) for k in ("cash", "short_term_investments", "receivables",
                                       "inventory", "prepaid_expenses", "other_current_assets")]
    parts = [p for p in parts if p is not None]
    return sum(parts) if parts else None


def _cl(bs):
    bs = bs or {}
    if bs.get("current_liabilities") is not None:
        return _num(bs["current_liabilities"])
    parts = [_num(bs.get(k)) for k in ("payables", "accrued_expenses", "notes_payable_st",
                                       "current_lt_debt", "other_current_liabilities")]
    parts = [p for p in parts if p is not None]
    return sum(parts) if parts else None


def _altman_z(statements, years):
    """Altman Z''-score (emerging-market, 4-variable form — no asset-turnover term,
    so it applies to service firms too):
        Z'' = 3.25 + 6.56·X1 + 3.26·X2 + 6.72·X3 + 1.05·X4
    X1 = working capital / assets, X2 = retained earnings / assets,
    X3 = EBIT / assets, X4 = book equity / total liabilities.
    Zones: > 2.6 safe · 1.1–2.6 grey · < 1.1 distress."""
    y = years[-1]
    bs = (statements.get(y, {}) or {}).get("BS", {}) or {}
    g = lambda s, i: _num(_v(statements, y, s, i))
    ta = g("BS", "total_assets")
    ca, cl = _ca(bs), _cl(bs)
    wc = (ca - cl) if (ca is not None and cl is not None) else None
    re = g("BS", "reserves")
    ebit = g("PL", "ebit")
    tl = g("BS", "total_liabilities")
    nw = g("BS", "net_worth") or g("BS", "equity")
    if not ta or not tl or None in (wc, re, ebit, nw):
        return None
    x1, x2, x3, x4 = wc / ta, re / ta, ebit / ta, nw / tl
    z = 3.25 + 6.56 * x1 + 3.26 * x2 + 6.72 * x3 + 1.05 * x4
    zone = "safe" if z > 2.6 else "grey" if z >= 1.1 else "distress"
    flag = {"safe": "green", "grey": "amber", "distress": "red"}[zone]
    return {"value": round(z, 2), "zone": zone, "flag": flag,
            "note": f"Z''={z:.2f} — {zone} zone (EM-adjusted bankruptcy distance)"}


def _beneish_m(statements, years):
    """Beneish M-score — likelihood of earnings manipulation. M > −1.78 flags
    elevated risk. SG&A isn't reported in this feed, so the minor SGAI term is
    held neutral (=1); the other seven variables use real data."""
    if len(years) < 2:
        return None
    t, p = years[-1], years[-2]
    P = lambda yr, i: _num(_v(statements, yr, "PL", i))
    B = lambda yr, i: _num(_v(statements, yr, "BS", i))
    C = lambda yr, i: _num(_v(statements, yr, "CF", i))
    salt, salp = P(t, "revenue"), P(p, "revenue")
    tat, tap = B(t, "total_assets"), B(p, "total_assets")
    if not salt or not salp or not tat:
        return None

    rect, recp = B(t, "receivables"), B(p, "receivables")
    dsri = ((rect / salt) / (recp / salp)) if (rect and recp) else 1.0

    gpt, gpp = P(t, "gross_profit"), P(p, "gross_profit")
    gmt = (gpt / salt) if gpt is not None else None
    gmp = (gpp / salp) if gpp is not None else None
    gmi = (gmp / gmt) if (gmt and gmp) else 1.0

    ppet, ppep = B(t, "fixed_assets"), B(p, "fixed_assets")
    def _aq(ca, ppe, ta):
        if None in (ca, ppe, ta) or not ta:
            return None
        return 1 - (ca + ppe) / ta
    aq_t = _aq(_ca((statements.get(t, {}) or {}).get("BS", {})), ppet, tat)
    aq_p = _aq(_ca((statements.get(p, {}) or {}).get("BS", {})), ppep, tap)
    aqi = (aq_t / aq_p) if (aq_t and aq_p) else 1.0

    sgi = salt / salp

    dept, depp = abs(P(t, "depreciation") or 0), abs(P(p, "depreciation") or 0)
    def _dep(dep, ppe):
        return dep / (dep + ppe) if (ppe and (dep + ppe)) else None
    d_t, d_p = _dep(dept, ppet), _dep(depp, ppep)
    depi = (d_p / d_t) if (d_t and d_p) else 1.0

    sgai = 1.0   # SG&A not reported → neutral

    def _lev(yr, ta):
        ltd = B(yr, "lt_debt"); cl = _cl((statements.get(yr, {}) or {}).get("BS", {}))
        return ((ltd + cl) / ta) if (ta and ltd is not None and cl is not None) else None
    lt, lp = _lev(t, tat), _lev(p, tap)
    lvgi = (lt / lp) if (lt and lp) else 1.0

    pat, cfo = P(t, "pat"), C(t, "operating_cf")
    tata = ((pat - cfo) / tat) if (pat is not None and cfo is not None and tat) else 0.0

    m = (-4.84 + 0.92 * dsri + 0.528 * gmi + 0.404 * aqi + 0.892 * sgi
         + 0.115 * depi - 0.172 * sgai + 4.679 * tata - 0.327 * lvgi)
    flag = "red" if m > -1.78 else "amber" if m > -2.22 else "green"
    return {"value": round(m, 2), "flag": flag, "threshold": -1.78,
            "sgai_neutral": True,
            "components": {"DSRI": round(dsri, 2), "GMI": round(gmi, 2), "AQI": round(aqi, 2),
                           "SGI": round(sgi, 2), "DEPI": round(depi, 2),
                           "LVGI": round(lvgi, 2), "TATA": round(tata, 3)},
            "note": ("M=%.2f — %s" % (m, "above −1.78: elevated manipulation risk"
                     if m > -1.78 else "below −1.78: no manipulation signal"))}


def forensic_report(statements: dict, co: dict) -> dict:
    statements = statements or {}
    years = sorted(int(y) for y in statements.keys())
    is_fin = (co or {}).get("type") == "financial"

    if len(years) < 2:
        return {"available": False, "composite": None, "grade": None,
                "metrics": {}, "flags": [],
                "pending": ["Promoter pledge % — not currently ingested"],
                "note": "Not enough statement history for forensic analysis."}

    y, p3 = years[-1], years[max(0, len(years) - 4)]   # latest, ~3y ago
    g = lambda yr, s, i: _num(_v(statements, yr, s, i))
    metrics, flags = {}, []

    # ── Sloan accrual ratio: (PAT − CFO) / avg total assets. High +ve = earnings
    #    propped up by accruals, not cash → lower quality.
    pat, cfo = g(y, "PL", "pat"), g(y, "CF", "operating_cf")
    ta_now, ta_prev = g(y, "BS", "total_assets"), g(years[-2], "BS", "total_assets")
    ta_avg = _avg([ta_now, ta_prev])
    if pat is not None and cfo is not None and ta_avg:
        accr = (pat - cfo) / ta_avg
        fl = _flag(abs(accr), 0.10, 0.25, higher_is_better=False)
        metrics["accrual_ratio"] = {"value": round(accr, 4), "flag": fl,
            "note": f"{accr*100:+.1f}% of assets — {'cash-backed' if fl=='green' else 'accrual-heavy' if fl=='red' else 'moderate'} earnings"}
        if fl == "red":
            flags.append({"level": "red", "label": "High accruals",
                          "note": f"Earnings exceed operating cash by {accr*100:.0f}% of assets — watch earnings quality."})

    # ── Cash conversion: 3y average CFO / PAT.
    cfo_s = [g(yr, "CF", "operating_cf") for yr in years[-3:]]
    pat_s = [g(yr, "PL", "pat") for yr in years[-3:]]
    cc_ratios = [(c / pt) for c, pt in zip(cfo_s, pat_s) if c is not None and pt and pt > 0]
    cc = _avg(cc_ratios)
    if cc is not None:
        fl = _flag(cc, 0.80, 0.50)
        metrics["cash_conversion"] = {"value": round(cc, 3), "flag": fl,
            "note": f"3y avg CFO/PAT {cc*100:.0f}% — {'strong' if fl=='green' else 'weak' if fl=='red' else 'fair'} cash backing"}
        if fl == "red":
            flags.append({"level": "red", "label": "Weak cash conversion",
                          "note": f"Only {cc*100:.0f}% of reported profit shows up as operating cash (3y avg)."})

    # ── Interest coverage: EBIT / interest expense.
    ebit, intr = g(y, "PL", "ebit"), g(y, "PL", "interest_expense")
    if ebit is not None and intr is not None and intr > 0:
        cov = ebit / intr
        fl = _flag(cov, 6, 3)
        metrics["interest_coverage"] = {"value": round(cov, 2), "flag": fl,
            "note": f"EBIT covers interest {cov:.1f}× — {'comfortable' if fl=='green' else 'thin' if fl=='red' else 'adequate'}"}
        if cov < 3:
            flags.append({"level": "red" if cov < 1.5 else "amber", "label": "Thin interest coverage",
                          "note": f"EBIT covers interest only {cov:.1f}× — debt-servicing pressure."})

    # ── Net debt / EBITDA.
    borrow, cash, ebitda = g(y, "BS", "borrowings"), g(y, "BS", "cash"), g(y, "PL", "ebitda")
    if borrow is not None and ebitda is not None and ebitda > 0:
        nd = borrow - (cash or 0)
        nde = nd / ebitda
        fl = _flag(nde, 1.0, 3.0, higher_is_better=False)
        metrics["net_debt_ebitda"] = {"value": round(nde, 2), "flag": fl,
            "note": (f"net cash" if nd < 0 else f"{nde:.1f}× EBITDA") + (" — low leverage" if fl=='green' else " — elevated" if fl=='red' else "")}
        if nde > 4:
            flags.append({"level": "red", "label": "High leverage",
                          "note": f"Net debt is {nde:.1f}× EBITDA — balance-sheet risk."})

    # ── Leverage trend: D/E now vs ~3y ago.
    de_now = _de(g, y)
    de_old = _de(g, p3)
    if de_now is not None and de_old is not None:
        trend = de_now - de_old
        rising = trend > 0.10
        direction = ("rising" if trend > 0.10 else "edging up" if trend > 0.02
                     else "falling" if trend < -0.02 else "broadly stable")
        metrics["leverage_trend"] = {"value": round(trend, 3),
            "flag": "red" if rising else "green",
            "note": f"D/E {de_old:.2f}→{de_now:.2f} over ~3y ({direction})"}
        if rising:
            flags.append({"level": "amber", "label": "Rising leverage",
                          "note": f"Debt/equity climbed {de_old:.2f}→{de_now:.2f} over ~3 years."})

    # ── Piotroski F (non-financials; the test set assumes an industrial P&L).
    pio = None if is_fin else _piotroski(statements, years)
    if pio:
        metrics["piotroski"] = pio
        if pio["normalized"] <= 3:
            flags.append({"level": "amber", "label": "Low Piotroski score",
                          "note": f"F-score {pio['score']}/{pio['max']} — weak fundamental momentum."})

    # ── Classic distress / manipulation scores (non-financials — neither the
    #    Altman manufacturing model nor Beneish maps to a lender's balance sheet).
    altman = None if is_fin else _altman_z(statements, years)
    if altman:
        metrics["altman_z"] = altman
        if altman["flag"] == "red":
            flags.append({"level": "red", "label": "Altman distress zone",
                          "note": f"Z''={altman['value']} sits in the distress zone (<1.1)."})
        elif altman["flag"] == "amber":
            flags.append({"level": "amber", "label": "Altman grey zone",
                          "note": f"Z''={altman['value']} is in the grey zone (1.1–2.6) — monitor solvency."})

    beneish = None if is_fin else _beneish_m(statements, years)
    if beneish:
        metrics["beneish_m"] = beneish
        if beneish["flag"] == "red":
            flags.append({"level": "red", "label": "Beneish manipulation signal",
                          "note": f"M-score {beneish['value']} exceeds −1.78 — screen earnings quality closely."})

    # Pending = whatever still couldn't be computed (pledge is never ingested yet).
    pending = ["Promoter pledge % — not currently ingested (governance signal)"]
    if not is_fin and not altman:
        pending.insert(0, "Altman Z″ — insufficient balance-sheet detail for this name")
    if not is_fin and not beneish:
        pending.insert(0, "Beneish M-score — insufficient year-over-year detail for this name")

    # ── Composite (0-100), reweighted over what computed.
    comp_parts = []
    if pio: comp_parts.append((pio["normalized"] / 9 * 100, 0.30))
    for key, w in (("accrual_ratio", 0.20), ("cash_conversion", 0.20),
                   ("interest_coverage", 0.15), ("net_debt_ebitda", 0.15)):
        m = metrics.get(key)
        if m and m.get("flag"):
            comp_parts.append((_score_from_flag(m["flag"]), w))
    composite = None
    if comp_parts:
        wsum = sum(w for _, w in comp_parts)
        composite = round(sum(s * w for s, w in comp_parts) / wsum, 1)

    grade = (None if composite is None
             else "A" if composite >= 80 else "B" if composite >= 65
             else "C" if composite >= 45 else "D")

    return {"available": bool(metrics), "composite": composite, "grade": grade,
            "is_financial": is_fin, "metrics": metrics, "flags": flags,
            "pending": pending,
            "note": ("Reduced set — a lender's P&L doesn't map to coverage/accrual mechanics; "
                     "see GNPA / CRAR / capital metrics on the Verdict tab." if is_fin else None)}


def _de(g, yr):
    nw = g(yr, "BS", "net_worth") or g(yr, "BS", "equity")
    b = g(yr, "BS", "borrowings")
    if nw and nw > 0 and b is not None:
        return b / nw
    return None
