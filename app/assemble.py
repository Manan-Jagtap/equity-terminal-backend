"""
Turn the normalized DB rows into the flat driver dicts the engines expect.

This is the seam between storage and computation: the engines never touch the
ORM, they just receive a clean dict. Swap the data source (XBRL, API) without
changing a line of engine code.
"""
from sqlalchemy.orm import Session
from . import models, concepts as K


def latest_facts(db: Session, company_id: int) -> dict:
    """Return {concept: value}, taking the most recent fiscal_year per concept."""
    rows = db.query(models.FinancialFact).filter_by(company_id=company_id).all()
    best: dict = {}
    for r in rows:
        cur = best.get(r.concept)
        if cur is None or r.fiscal_year > cur[0]:
            best[r.concept] = (r.fiscal_year, r.value)
    return {k: v[1] for k, v in best.items()}


def assumptions_dict(asm: models.Assumptions) -> dict:
    return {
        "beta": asm.beta, "risk_free": asm.risk_free, "erp": asm.erp,
        "forecast_roe": asm.forecast_roe, "terminal_roe": asm.terminal_roe, "payout": asm.payout,
        "rev_growth": asm.rev_growth, "ebit_margin": asm.ebit_margin, "tax_rate": asm.tax_rate,
        "reinvest_rate": asm.reinvest_rate, "debt_weight": asm.debt_weight, "cost_debt": asm.cost_debt,
        "fade_years": asm.fade_years, "terminal_growth": asm.terminal_growth,
    }


def build_company(db: Session, co: models.Company) -> dict:
    facts = latest_facts(db, co.id)
    price = co.market.price if co.market else 0.0
    series = [{"i": p.t, "close": p.close} for p in sorted(co.prices, key=lambda x: x.t)]

    out = {
        "id": co.id, "ticker": co.ticker, "name": co.name, "type": co.type,
        "sector": co.sector, "shares": co.shares_outstanding, "price": price,
        "equity": facts.get(K.NET_WORTH), "net_profit": facts.get(K.NET_PROFIT),
        "series": series,
    }
    if co.type == "financial":
        out["nbfc"] = {
            "aum": facts.get(K.AUM), "gnpa": facts.get(K.GNPA), "nnpa": facts.get(K.NNPA),
            "crar": facts.get(K.CRAR), "nim": facts.get(K.NIM), "roa": facts.get(K.ROA),
        }
    else:
        out["revenue"] = facts.get(K.REVENUE)
        out["net_debt"] = facts.get(K.NET_DEBT)
    return out
