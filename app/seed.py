"""
Seed the database with the same illustrative universe as the React app.
Run once:  python -m app.seed
Re-run safely: it drops and recreates everything.

Replace these numbers with real figures (ideally ingested from XBRL) when ready.
"""
from .database import Base, engine, SessionLocal
from . import models, concepts as K


# --- identical PRNG to the frontend so price series (and technicals) match ---
def rng(seed):
    s = seed % 2147483647
    if s <= 0:
        s += 2147483646
    state = {"s": s}

    def nxt():
        state["s"] = (state["s"] * 16807) % 2147483647
        return state["s"] / 2147483647
    return nxt


def make_series(seed, start, drift, vol, n=250):
    r = rng(seed)
    out, p = [], start
    for i in range(n):
        p = p * (1 + drift / n + (r() - 0.5) * vol)
        out.append((i, round(p, 1)))
    return out


FY = 2026

# (ticker, name, type, sector, shares, price, facts{}, assumptions{}, series_params)
UNIVERSE = [
    ("MUTHOOTFIN", "Muthoot Finance", "financial", "Gold Loan NBFC", 40.1, 1980,
     {K.NET_WORTH: 26500, K.NET_PROFIT: 4470, K.AUM: 92000, K.GNPA: 0.029, K.NNPA: 0.026, K.CRAR: 0.288, K.NIM: 0.115, K.ROA: 0.052},
     dict(beta=1.05, risk_free=0.069, erp=0.065, forecast_roe=0.225, terminal_roe=0.155, payout=0.22, fade_years=8, terminal_growth=0.05),
     (11, 1650, 0.20, 0.022)),
    ("MANAPPURAM", "Manappuram Finance", "financial", "Gold Loan NBFC", 84.6, 178,
     {K.NET_WORTH: 11900, K.NET_PROFIT: 2200, K.AUM: 44000, K.GNPA: 0.045, K.NNPA: 0.040, K.CRAR: 0.302, K.NIM: 0.135, K.ROA: 0.048},
     dict(beta=1.20, risk_free=0.069, erp=0.065, forecast_roe=0.195, terminal_roe=0.140, payout=0.20, fade_years=8, terminal_growth=0.045),
     (23, 150, 0.16, 0.028)),
    ("FEDFINA", "Fedbank Financial (Fedfina)", "financial", "Diversified NBFC", 37.0, 118,
     {K.NET_WORTH: 2400, K.NET_PROFIT: 280, K.AUM: 14500, K.GNPA: 0.020, K.NNPA: 0.015, K.CRAR: 0.205, K.NIM: 0.080, K.ROA: 0.022},
     dict(beta=1.10, risk_free=0.069, erp=0.065, forecast_roe=0.135, terminal_roe=0.135, payout=0.0, fade_years=9, terminal_growth=0.06),
     (37, 95, 0.22, 0.030)),
    ("IIFL", "IIFL Finance", "financial", "Diversified NBFC", 42.5, 430,
     {K.NET_WORTH: 11200, K.NET_PROFIT: 1550, K.AUM: 78000, K.GNPA: 0.024, K.NNPA: 0.012, K.CRAR: 0.213, K.NIM: 0.090, K.ROA: 0.024},
     dict(beta=1.30, risk_free=0.069, erp=0.065, forecast_roe=0.165, terminal_roe=0.140, payout=0.18, fade_years=8, terminal_growth=0.05),
     (53, 520, -0.10, 0.035)),
    ("BAJFINANCE", "Bajaj Finance", "financial", "Diversified NBFC", 62.0, 7100,
     {K.NET_WORTH: 95000, K.NET_PROFIT: 16700, K.AUM: 410000, K.GNPA: 0.010, K.NNPA: 0.004, K.CRAR: 0.219, K.NIM: 0.105, K.ROA: 0.045},
     dict(beta=1.15, risk_free=0.069, erp=0.065, forecast_roe=0.215, terminal_roe=0.160, payout=0.10, fade_years=10, terminal_growth=0.06),
     (71, 6600, 0.14, 0.020)),
    ("TITAN", "Titan Company", "nonfinancial", "Consumer / Retail", 88.8, 3450,
     {K.NET_WORTH: 12000, K.NET_PROFIT: 3900, K.REVENUE: 56000, K.NET_DEBT: 8000},
     dict(beta=0.95, risk_free=0.069, erp=0.065, ebit_margin=0.115, tax_rate=0.25, reinvest_rate=0.40,
          rev_growth=0.135, debt_weight=0.15, cost_debt=0.085, fade_years=9, terminal_growth=0.055),
     (91, 3200, 0.12, 0.021)),
]


def run():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        for ticker, name, ctype, sector, shares, price, facts, asm, sp in UNIVERSE:
            co = models.Company(ticker=ticker, name=name, type=ctype, sector=sector,
                                shares_outstanding=shares)
            db.add(co); db.flush()

            for concept, value in facts.items():
                unit = "RATIO" if concept in (K.GNPA, K.NNPA, K.CRAR, K.NIM, K.ROA) else "INR_CR"
                db.add(models.FinancialFact(company_id=co.id, fiscal_year=FY, period="FY",
                                            concept=concept, value=value, unit=unit, source="seed"))

            db.add(models.Assumptions(company_id=co.id, **asm))
            db.add(models.MarketSnapshot(company_id=co.id, price=price))
            for i, close in make_series(*sp):
                db.add(models.PricePoint(company_id=co.id, t=i, close=close))

        db.commit()
        print(f"Seeded {len(UNIVERSE)} companies.")
    finally:
        db.close()


if __name__ == "__main__":
    run()
