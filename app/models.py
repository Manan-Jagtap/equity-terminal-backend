"""
ORM models — the normalized store.

Design notes:
- Company holds identity only (ticker, type, sector, shares).
- FinancialFact is the time-series normalization layer: one row per
  (company, fiscal_year, period, canonical concept). This is what scales to
  "ingest any filing and map its tags into our concepts." Querying the latest
  year per concept gives the engine its drivers.
- Assumptions holds the valuation inputs the user tweaks (CAPM + model drivers).
- MarketSnapshot holds the latest price; PricePoint holds the series for technicals.
"""
from sqlalchemy import (
    Column, Integer, String, Float, ForeignKey, UniqueConstraint, DateTime, func,
)
from sqlalchemy.orm import relationship
from .database import Base


class Company(Base):
    __tablename__ = "companies"
    id = Column(Integer, primary_key=True)
    ticker = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    type = Column(String, nullable=False)          # "financial" | "nonfinancial"
    sector = Column(String, nullable=False)
    shares_outstanding = Column(Float, nullable=False)  # crore

    facts = relationship("FinancialFact", back_populates="company", cascade="all, delete-orphan")
    assumptions = relationship("Assumptions", back_populates="company", uselist=False, cascade="all, delete-orphan")
    market = relationship("MarketSnapshot", back_populates="company", uselist=False, cascade="all, delete-orphan")
    prices = relationship("PricePoint", back_populates="company", cascade="all, delete-orphan")


class FinancialFact(Base):
    __tablename__ = "financial_facts"
    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    fiscal_year = Column(Integer, nullable=False)        # e.g. 2026
    period = Column(String, default="FY")                # "FY" | "Q1".. for quarterly
    concept = Column(String, nullable=False)             # canonical code from concepts.py
    value = Column(Float, nullable=False)
    unit = Column(String, default="INR_CR")              # or "RATIO"
    source = Column(String, default="seed")              # "xbrl" | "annual_report" | "api" | "seed"

    company = relationship("Company", back_populates="facts")
    __table_args__ = (UniqueConstraint("company_id", "fiscal_year", "period", "concept", name="uq_fact"),)


class Assumptions(Base):
    __tablename__ = "assumptions"
    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), unique=True, nullable=False)

    # CAPM (all company types)
    beta = Column(Float, default=1.0)
    risk_free = Column(Float, default=0.069)
    erp = Column(Float, default=0.065)

    # Residual-income drivers (financials)
    forecast_roe = Column(Float, nullable=True)
    terminal_roe = Column(Float, nullable=True)
    payout = Column(Float, nullable=True)

    # FCFF drivers (non-financials)
    rev_growth = Column(Float, nullable=True)
    ebit_margin = Column(Float, nullable=True)
    tax_rate = Column(Float, nullable=True)
    reinvest_rate = Column(Float, nullable=True)
    debt_weight = Column(Float, nullable=True)
    cost_debt = Column(Float, nullable=True)

    # Shared
    fade_years = Column(Float, default=8)
    terminal_growth = Column(Float, default=0.05)

    company = relationship("Company", back_populates="assumptions")


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"
    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), unique=True, nullable=False)
    price = Column(Float, nullable=False)
    as_of = Column(DateTime, server_default=func.now())

    company = relationship("Company", back_populates="market")


class PricePoint(Base):
    __tablename__ = "price_points"
    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    t = Column(Integer, nullable=False)   # sequence index (replace with real dates)
    close = Column(Float, nullable=False)

    company = relationship("Company", back_populates="prices")
