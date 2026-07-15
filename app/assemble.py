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

    out = {
        "id": co.id, "ticker": co.ticker, "name": co.name,
        "type": co.type, "sector": co.sector,
        "shares": co.shares_outstanding if (co.shares_outstanding and co.shares_outstanding > 0) else None,
        "price": price, "equity": equity, "net_profit": net_profit,
        "series": series, "synthetic_series": synthetic_series,
        "synthetic_price": real_price is None,
    }

    if co.type == "financial":
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

    # Attach the 5-year statement history + valuation sector so the engine can
    # derive forward drivers from the company's OWN data (see derive.py).
    template_code = co.template_code or T.classify(co.sector)
    is_fin = T.is_financial(template_code) or co.type == "financial"
    out["template_code"] = template_code
    out["is_financial_template"] = is_fin
    out["valuation_sector"] = (SP.TICKER_OVERRIDES.get((co.ticker or "").upper())
                               or SP.classify_valuation_sector(co.sector, template_code))

    hist_rows = (db.query(models.HistoricalFinancial)
                   .filter_by(company_id=co.id).all())
    out["statements"] = _shape_statements(hist_rows)

    return out


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
    return a
