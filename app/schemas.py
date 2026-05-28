"""Request body for recomputing valuation with user-tweaked assumptions."""
from typing import Optional
from pydantic import BaseModel


class AssumptionOverride(BaseModel):
    # any subset can be sent; missing fields fall back to the stored assumptions
    beta: Optional[float] = None
    risk_free: Optional[float] = None
    erp: Optional[float] = None
    forecast_roe: Optional[float] = None
    terminal_roe: Optional[float] = None
    payout: Optional[float] = None
    rev_growth: Optional[float] = None
    ebit_margin: Optional[float] = None
    tax_rate: Optional[float] = None
    reinvest_rate: Optional[float] = None
    debt_weight: Optional[float] = None
    cost_debt: Optional[float] = None
    fade_years: Optional[float] = None
    terminal_growth: Optional[float] = None
    price: Optional[float] = None   # let the user test a different market price
