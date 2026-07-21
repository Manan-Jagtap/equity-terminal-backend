"""
Turn DB rows into flat dicts the engines consume.
All fields have safe defaults so None values never crash the engines.
"""
from sqlalchemy.orm import Session
from . import models, concepts as K
from . import templates as T
from . import sector_params as SP
from .derive import derive_assumptions


def safe(val, default=0.0):
    return val if val is not None else default


def latest_facts(db: Session, company_id: int) -> dict:
    rows = db.query(models.FinancialFact).filter_by(company_id=company_id).all()
    best = {}
    for r in rows:
        cur = best.get(r.concept)
        if cur is None or r.fiscal_year > cur[0]:
            best[r.concept] = (r.fiscal_year, r.value)
    return {k: v[1] for k, v in best.items()}


def assumptions_dict(asm: models.Assumptions) -> dict:
    return {
        "beta":         safe(asm.beta, 1.0),
        "risk_free":    safe(asm.risk_free, 0.069),
        "erp":          safe(asm.erp, 0.065),
        "forecast_roe": safe(asm.forecast_roe, 0.15),
        "terminal_roe": safe(asm.terminal_roe, 0.13),
        "payout":       safe(asm.payout, 0.20),
        "rev_growth":   safe(asm.rev_growth, 0.10),
        "ebit_margin":  safe(asm.ebit_margin, 0.12),
        "tax_rate":     safe(asm.tax_rate, 0.25),
        "reinvest_rate":safe(asm.reinvest_rate, 0.35),
        "debt_weight":  safe(asm.debt_weight, 0.20),
        "cost_debt":    safe(asm.cost_debt, 0.085),
        "fade_years":   safe(asm.fade_years, 8),
        "terminal_growth": safe(asm.terminal_growth, 0.05),
    }


def build_company(db: Session, co: models.Company) -> dict:
    facts = latest_facts(db, co.id)
    # A real, positive live price or nothing. We keep a 1.0 sentinel so charts and
    # ratio math don't divide by None, but we FLAG it (synthetic_price) so the
    # trust layer drops confidence — a missing price paired with a real intrinsic
    # would otherwise fabricate a huge bogus margin of safety / "BUY".
    real_price = co.market.price if co.market else None
    real_price = real_price if (real_price and real_price > 0) else None
    price = real_price if real_price is not None else 1.0
    series = [{"i": p.t, "close": p.close}
              for p in sorted(co.prices, key=lambda x: x.t)]

    # need at least 20 price points for technicals; if we don't have real OHLC
    # we synthesize a flat-ish series ONLY to keep charts from crashing, and we
    # flag it so the trust layer knows momentum/52W are not real.
    synthetic_series = len(series) < 20
    if synthetic_series:
        base = price
        series = [{"i": i, "close": round(base * (1 + (i - 10) * 0.001), 2)}
                  for i in range(50)]

    # Do NOT fabricate fundamentals. Missing values stay None and flow through to
    # the trust layer, which lowers confidence and shows the gaps. Previously these
    # were invented (equity = price×10, revenue = price×shares×0.5, net_debt = 0),
    # which silently produced confident, wrong valuations.
    equity     = facts.get(K.NET_WORTH)
    net_profit = facts.get(K.NET_PROFIT)

    # FIX-12 (type↔sector reconciliation): the resolved valuation SECTOR is the
    # source of truth for WHICH model runs. A name mapped to a financial sector
    # (BANK/NBFC/INSURANCE) but stored with type≠financial — the 12 mis-typed
    # brokers/holdcos (ARSSBL, EDELWEISS, RELIGARE, …) — otherwise runs the
    # FCFF/WACC-DCF path on a balance sheet where debt is raw material (the
    # classic category error: ARSSBL printed a DCF BUY at +176%). Reconcile the
    # type HERE, before the asset-quality vs revenue split, so the engine, the
    # derived assumptions and `_is_fee_financial` all agree and the brokers get
    # an honest financial/fee verdict instead of a fabricated FCFF upside.
    template_code = co.template_code or T.classify(co.sector)
    valuation_sector = (SP.TICKER_OVERRIDES.get((co.ticker or "").upper())
                        or SP.classify_valuation_sector(co.sector, template_code))
    eff_type = "financial" if valuation_sector in SP.FINANCIAL_VSECTORS else co.type
    is_fin = T.is_financial(template_code) or eff_type == "financial"

    out = {
        "id": co.id, "ticker": co.ticker, "name": co.name,
        "type": eff_type, "sector": co.sector,
        "shares": co.shares_outstanding if (co.shares_outstanding and co.shares_outstanding > 0) else None,
        "price": price, "equity": equity, "net_profit": net_profit,
        "series": series, "synthetic_series": synthetic_series,
        "synthetic_price": real_price is None,
        "template_code": template_code,
        "is_financial_template": is_fin,
        "valuation_sector": valuation_sector,
    }

    if eff_type == "financial":
        out["nbfc"] = {
            "aum":  facts.get(K.AUM),
            "gnpa": facts.get(K.GNPA),
            "nnpa": facts.get(K.NNPA),
            "crar": facts.get(K.CRAR),
            "nim":  facts.get(K.NIM),
            "roa":  facts.get(K.ROA),
        }
    else:
        out["revenue"]  = facts.get(K.REVENUE)
        out["net_debt"] = facts.get(K.NET_DEBT)

    hist_rows = (db.query(models.HistoricalFinancial)
                   .filter_by(company_id=co.id).all())
    out["statements"] = _shape_statements(hist_rows)

    # FIX-09: margin-based DISTRIBUTION-economics override. A thin-margin,
    # high-revenue name in a full-margin consumer/industrial sector earns its
    # value like a DISTRIBUTOR (pass-through topline, structurally low margins),
    # not a franchise — capitalising that topline at CONSUMER/MANUFACTURING
    # multiples over-values it (AWL, a ₹58k-cr edible-oil distributor at ~1.3%
    # through-cycle net margin, printed a rich BUY). Route it to DISTRIBUTION's
    # low multiples. GUARD: only the full-margin sectors are eligible — cyclicals
    # (METAL/ENERGY/CEMENT/CHEMICALS/AUTO…) carry their own through-cycle models
    # and are NEVER re-bucketed; the ₹5k-cr floor keeps small specialty names out.
    if out["valuation_sector"] in _DIST_ELIGIBLE:
        tc_npm, tc_rev = _through_cycle_npm(out["statements"])
        eq = out.get("equity")
        # Revenue/equity turnover separates a DISTRIBUTOR (capital-light,
        # pass-through volume → turns its equity many times in revenue) from a
        # thin-net-margin BRAND (owns capacity/brand, lower turns, fatter gross
        # margin narrowed by A&P/interest). Without it the rule wrongly swept in
        # UBL / VOLTAS / WHIRLPOOL and UNDER-valued them. The net-margin FLOOR
        # keeps out near-zero / loss names (ABDL, TVSSCS) — those are a data /
        # loss-maker case the existing low-ROE gate abstains, not distribution.
        turnover = (tc_rev / eq) if (tc_rev and eq and eq > 0) else None
        if (tc_npm is not None and 0.005 < tc_npm < 0.04
                and tc_rev is not None and tc_rev > 5000
                and turnover is not None and turnover > 2.5):
            out["_distribution_override"] = (
                f"{out['valuation_sector']}→DISTRIBUTION: through-cycle net margin "
                f"{tc_npm*100:.1f}% (0.5–4%) at {turnover:.1f}× revenue/equity turnover "
                f"on ₹{tc_rev:,.0f}cr — capital-light distribution economics, not a "
                "full-margin franchise (FIX-09).")
            out["valuation_sector"] = "DISTRIBUTION"

    return out


# Full-margin sectors eligible for the FIX-09 DISTRIBUTION override. Cyclicals
# (METAL/ENERGY/CEMENT/CHEMICALS/AUTO/AVIATION/…) are deliberately excluded so a
# thin down-cycle margin never re-buckets a genuine cyclical.
_DIST_ELIGIBLE = frozenset({"CONSUMER", "CONSUMER_DISC", "MANUFACTURING"})


def _through_cycle_npm(statements: dict) -> tuple[float | None, float | None]:
    """(median net-profit margin, median revenue in ₹cr) across the available
    fiscal years, from the same canonical PL keys derive.py uses. None when there
    isn't a usable revenue+PAT pair — a name we can't margin-classify keeps its
    sector, never gets force-bucketed on missing data."""
    import statistics
    pats, revs = [], []
    for y in (statements or {}):
        pl = (statements[y] or {}).get("PL") or {}
        rev, pat = pl.get("revenue"), pl.get("pat")
        if rev and rev > 0 and pat is not None:
            revs.append(rev); pats.append(pat)
    if not revs:
        return None, None
    return statistics.median(p / r for p, r in zip(pats, revs)), statistics.median(revs)


def _shape_statements(hist_rows) -> dict:
    """Nested { year:int -> { 'PL':{item:val}, 'BS':{...}, 'CF':{...} } } from the
    last 5 fiscal years — the shape derive.py and the metrics layer expect.

    Runs the SAME integrity gate the Financials tab uses (misfiled-year columns,
    sign-flipped cost lines, >80% YoY collapses). Previously only financials.py
    sanitized, so the DCF/RI intrinsic, the AI thesis and the one-pager — the
    highest-stakes outputs — were computed from un-gated statements."""
    nested: dict = {}
    for r in hist_rows:
        if r.value is None:
            continue
        nested.setdefault(int(r.fiscal_year), {}).setdefault(r.statement_type, {})[r.line_item] = r.value
    years = sorted(nested.keys())[-5:]
    out = {y: nested[y] for y in years}
    try:
        from app.financials import _sanitize_statements
        financial = any(("nii" in (out[y].get("PL") or {})) or
                        ("interest_income" in (out[y].get("PL") or {})) for y in out)
        _sanitize_statements(out, financial)
    except Exception:
        pass          # a sanitizer hiccup must never break statement assembly
    return out


def effective_assumptions(db: Session, co: models.Company, data: dict) -> dict:
    """The INDEPENDENT assumption block used for valuation: derived from the
    company's own stored history + sector_params. Replaces the old yfinance-
    seeded Assumptions rows as the live default."""
    # Discount at TODAY's curve on the on-demand (web) path too — not just the
    # nightly batch. TTL-cached, so this is one cheap read at most every 30 min
    # per process, and every recompute surface (detail page, what-if, one-pager,
    # Excel) now prices off the same live 10Y G-sec as the stored screener rows.
    SP.refresh_risk_free(db)
    vs = data.get("valuation_sector") or SP.classify_valuation_sector(co.sector, co.template_code)
    is_fin = data.get("is_financial_template",
                      T.is_financial(co.template_code or T.classify(co.sector)) or co.type == "financial")
    a = derive_assumptions(data.get("statements") or {}, vs, is_fin)
    # Use the CALCULATED beta (regression vs the equal-weighted universe, shrunk
    # to the sector prior) when we have one; else keep the sector beta.
    try:
        from app import beta as _beta
        rec = _beta.beta_for(db, co.ticker)
        if rec and rec.get("beta"):
            a["beta"] = rec["beta"]
            drv = a.get("_drivers")
            if isinstance(drv, dict):
                drv["beta"] = (f"calc β {rec['beta']} = shrink(raw {rec['raw']} vs "
                               f"{rec.get('market', 'market')}, R² {rec['r2']}, n={rec['n']}) "
                               f"→ sector prior {rec['prior']}")
    except Exception:
        pass
    # Data-driven segment SOTP (reported segment EBIT × sector multiples), when it
    # has been extracted for this name — attached so alternative_intrinsic() can
    # prefer it over the illustrative preset. Guarded; never breaks the valuation.
    try:
        from app.segment_sotp import get_segment_sotp
        seg = get_segment_sotp(db, co.ticker, data.get("net_debt") or 0.0,
                               data.get("shares") or co.shares_outstanding or 0.0)
        if seg:
            a["_segment_sotp"] = seg
    except Exception:
        pass
    return a
