"""
models.py — updated with 5-year historical storage.

New tables:
  HistoricalFinancial  — annual P&L / BS / CF line items (5 years)
  HistoricalPrice      — daily OHLCV (5 years, replaces PricePoint)

Existing tables unchanged so migration is additive.
"""
from sqlalchemy import (
    Column, Integer, String, Float, ForeignKey,
    UniqueConstraint, DateTime, Date, func, JSON,
)
from sqlalchemy.orm import relationship
from .database import Base


class Company(Base):
    __tablename__ = "companies"
    id = Column(Integer, primary_key=True)
    ticker = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    type = Column(String, nullable=False)        # "financial" | "nonfinancial"
    sector = Column(String, nullable=False)
    template_code = Column(String(20), nullable=True, index=True)
    bse_scrip_code = Column(String(20), nullable=True, index=True)
    shares_outstanding = Column(Float, nullable=False)

    facts        = relationship("FinancialFact",      back_populates="company", cascade="all, delete-orphan")
    hist_fins    = relationship("HistoricalFinancial", back_populates="company", cascade="all, delete-orphan")
    assumptions  = relationship("Assumptions",         back_populates="company", uselist=False, cascade="all, delete-orphan")
    market       = relationship("MarketSnapshot",      back_populates="company", uselist=False, cascade="all, delete-orphan")
    prices       = relationship("PricePoint",          back_populates="company", cascade="all, delete-orphan")
    hist_prices  = relationship("HistoricalPrice",     back_populates="company", cascade="all, delete-orphan")


# ── Existing normalised facts (current year, used by screener) ──────────────
class FinancialFact(Base):
    __tablename__ = "financial_facts"
    id = Column(Integer, primary_key=True)
    company_id   = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    fiscal_year  = Column(Integer, nullable=False)
    period       = Column(String, default="FY")
    concept      = Column(String, nullable=False)
    value        = Column(Float, nullable=False)
    unit         = Column(String, default="INR_CR")
    source       = Column(String, default="seed")
    company      = relationship("Company", back_populates="facts")
    __table_args__ = (UniqueConstraint("company_id","fiscal_year","period","concept", name="uq_fact"),)


# ── NEW: 5-year historical financial statements ──────────────────────────────
class HistoricalFinancial(Base):
    """
    One row per (company, fiscal_year, statement_type, line_item).

    statement_type: "PL" | "BS" | "CF"
    line_item:      canonical name e.g. "revenue", "ebit", "pat",
                    "total_assets", "equity", "borrowings",
                    "operating_cf", "capex", "fcf"
    value:          ₹ crore
    source:         "yfinance" | "xbrl" | "manual" | "screener"
    """
    __tablename__ = "historical_financials"
    id             = Column(Integer, primary_key=True)
    company_id     = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    fiscal_year    = Column(Integer, nullable=False)   # e.g. 2021, 2022 … 2025
    statement_type = Column(String, nullable=False)    # PL | BS | CF
    line_item      = Column(String, nullable=False)    # canonical name
    value          = Column(Float, nullable=True)      # ₹ cr; None = not available
    source         = Column(String, default="yfinance")
    company        = relationship("Company", back_populates="hist_fins")
    __table_args__ = (UniqueConstraint("company_id","fiscal_year","statement_type","line_item", name="uq_hist_fin"),)


# ── Existing 1-year daily prices (kept for compatibility) ───────────────────
class PricePoint(Base):
    __tablename__ = "price_points"
    id         = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    t          = Column(Integer, nullable=False)
    close      = Column(Float, nullable=False)
    company    = relationship("Company", back_populates="prices")


# ── NEW: 5-year daily OHLCV ─────────────────────────────────────────────────
class HistoricalPrice(Base):
    """
    Full 5-year daily OHLCV.  date stored as ISO string "YYYY-MM-DD".
    """
    __tablename__ = "historical_prices"
    id         = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    date       = Column(String, nullable=False)   # "YYYY-MM-DD"
    open       = Column(Float, nullable=True)
    high       = Column(Float, nullable=True)
    low        = Column(Float, nullable=True)
    close      = Column(Float, nullable=False)
    volume     = Column(Float, nullable=True)
    company    = relationship("Company", back_populates="hist_prices")
    __table_args__ = (UniqueConstraint("company_id","date", name="uq_hist_price"),)


class Assumptions(Base):
    __tablename__ = "assumptions"
    id           = Column(Integer, primary_key=True)
    company_id   = Column(Integer, ForeignKey("companies.id"), unique=True, nullable=False)
    beta         = Column(Float, default=1.0)
    risk_free    = Column(Float, default=0.069)
    erp          = Column(Float, default=0.065)
    forecast_roe = Column(Float, nullable=True)
    terminal_roe = Column(Float, nullable=True)
    payout       = Column(Float, nullable=True)
    rev_growth   = Column(Float, nullable=True)
    ebit_margin  = Column(Float, nullable=True)
    tax_rate     = Column(Float, nullable=True)
    reinvest_rate= Column(Float, nullable=True)
    debt_weight  = Column(Float, nullable=True)
    cost_debt    = Column(Float, nullable=True)
    fade_years   = Column(Float, default=8)
    terminal_growth = Column(Float, default=0.05)
    company      = relationship("Company", back_populates="assumptions")


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"
    id         = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), unique=True, nullable=False)
    price      = Column(Float, nullable=False)
    as_of      = Column(DateTime, server_default=func.now())
    company    = relationship("Company", back_populates="market")


# ── NEW: analyst / forward / Screener-style insight blob (IndianAPI v2) ──────
class CompanyInsight(Base):
    """
    One JSON blob per company holding everything the valuation engine doesn't:
      analyst   — consensus rating, distribution, # analysts, mean target price
      forecasts — forward EPS / revenue (Estimates) → forward P/E
      peers     — peer comp table (P/E, P/B, ROE, margins, div yield)
      ratios    — Screener-style ROCE%, Debtor Days, Working Capital Days series
      growth    — compounded sales/profit growth + ROE over 10/5/3yr/TTM
      latest_q  — latest quarter + TTM (sales, net profit, EPS, OPM)
      ticker_id — IndianAPI internal id (S00xxxxx) for target/forecast calls
    Stored as JSON so new fields need no migration.
    """
    __tablename__ = "company_insights"
    id         = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), unique=True, nullable=False, index=True)
    ticker_id  = Column(String(20), nullable=True)
    data       = Column(JSON, nullable=False, default=dict)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    company    = relationship("Company", backref="insight", uselist=False)


class QuarterlyDocument(Base):
    __tablename__ = "quarterly_documents"
    __table_args__ = (
        UniqueConstraint("company_id", "quarter", "doc_type",
                         name="uq_qd_company_quarter_type"),
    )

    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    quarter = Column(String(10), nullable=False)
    doc_type = Column(String(20), nullable=False)

    bse_announcement_id = Column(String(100), nullable=True)
    bse_filing_date = Column(DateTime, nullable=True)
    source_url = Column(String, nullable=True)

    r2_key = Column(String, nullable=False)
    file_size_bytes = Column(Integer, nullable=True)

    extracted_text = Column(String, nullable=True)
    char_count = Column(Integer, nullable=True)

    fetched_at = Column(DateTime, default=func.now(), nullable=False)

    company = relationship("Company", backref="quarterly_documents")
