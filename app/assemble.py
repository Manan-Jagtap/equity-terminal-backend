"""
Turn DB rows into flat dicts the engines consume.
All fields have safe defaults so None values never crash the engines.
"""
from sqlalchemy.orm import Session
from . import models, concepts as K


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
    price = co.market.price if co.market else 1.0
    series = [{"i": p.t, "close": p.close}
              for p in sorted(co.prices, key=lambda x: x.t)]

    # need at least 20 price points for technicals
    if len(series) < 20:
        base = price
        series = [{"i": i, "close": round(base * (1 + (i - 10) * 0.001), 2)}
                  for i in range(50)]

    equity     = safe(facts.get(K.NET_WORTH), price * 10)
    net_profit = facts.get(K.NET_PROFIT)  # can be None — engines handle it

    out = {
        "id": co.id, "ticker": co.ticker, "name": co.name,
        "type": co.type, "sector": co.sector,
        "shares": max(co.shares_outstanding, 0.1),
        "price": price, "equity": equity, "net_profit": net_profit,
        "series": series,
    }

    if co.type == "financial":
        out["nbfc"] = {
            "aum":    safe(facts.get(K.AUM), equity * 4),
            "gnpa":   safe(facts.get(K.GNPA), 0.03),
            "nnpa":   safe(facts.get(K.NNPA), 0.015),
            "crar":   safe(facts.get(K.CRAR), 0.18),
            "nim":    safe(facts.get(K.NIM), 0.09),
            "roa":    safe(facts.get(K.ROA), 0.02),
        }
    else:
        out["revenue"]  = safe(facts.get(K.REVENUE), price * co.shares_outstanding * 0.5)
        out["net_debt"] = safe(facts.get(K.NET_DEBT), 0.0)

    return out
