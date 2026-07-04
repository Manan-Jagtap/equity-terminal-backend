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


class User(Base):
    """An account. Email is stored lowercased; password_hash is the
    pbkdf2$<iter>$<salt>$<hash> string produced by app.auth.hash_password.
    Personal rows (watchlist/portfolio) are scoped by user_key = f"u{id}"."""
    __tablename__ = "users"
    id            = Column(Integer, primary_key=True)
    email         = Column(String, unique=True, index=True, nullable=False)
    name          = Column(String, nullable=True)
    password_hash = Column(String, nullable=False)
    created_at    = Column(DateTime, server_default=func.now())


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


class Valuation(Base):
    """Precomputed INDEPENDENT valuation per company, so /api/companies is
    instant instead of running the full DCF + technicals on every request.
    Populated by app.ingest.compute_valuations. The analyst consensus is stored
    alongside but kept SEPARATE (never blended into `intrinsic`)."""
    __tablename__ = "valuations"
    id          = Column(Integer, primary_key=True)
    company_id  = Column(Integer, ForeignKey("companies.id"), unique=True, nullable=False, index=True)
    # Independent model
    intrinsic   = Column(Float, nullable=True)
    mos         = Column(Float, nullable=True)
    verdict     = Column(String, nullable=True)
    composite   = Column(Float, nullable=True)
    reliable    = Column(Integer, nullable=True)   # 1/0
    confidence  = Column(String, nullable=True)
    method      = Column(String, nullable=True)
    valuation_sector = Column(String, nullable=True)
    # Snapshot fundamentals (so the screener needn't recompute)
    roe         = Column(Float, nullable=True)
    pb          = Column(Float, nullable=True)
    pe          = Column(Float, nullable=True)
    # Analyst consensus (SEPARATE — for the consensus tab/column, never anchored)
    analyst_target = Column(Float, nullable=True)
    analyst_low    = Column(Float, nullable=True)
    analyst_high   = Column(Float, nullable=True)
    analyst_rating = Column(String, nullable=True)
    analyst_upside = Column(Float, nullable=True)
    updated_at  = Column(DateTime, server_default=func.now(), onupdate=func.now())
    company     = relationship("Company", backref="valuation", uselist=False)


class VerdictSnapshot(Base):
    """Daily record of the model's verdict + price per company — the raw ledger
    behind the Track Record (backtest) page. One row per (company, date);
    written by the scheduler after every EOD price refresh and boot recompute.
    Consecutive same-verdict rows are compressed into 'calls' at read time, so
    daily sampling never oversamples a single standing recommendation."""
    __tablename__ = "verdict_snapshots"
    id          = Column(Integer, primary_key=True)
    company_id  = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    ticker      = Column(String(24), nullable=False, index=True)
    date        = Column(String(10), nullable=False, index=True)   # "YYYY-MM-DD"
    price       = Column(Float, nullable=False)
    intrinsic   = Column(Float, nullable=True)
    mos         = Column(Float, nullable=True)
    verdict     = Column(String, nullable=False)
    composite   = Column(Float, nullable=True)
    confidence  = Column(String, nullable=True)
    valuation_sector = Column(String, nullable=True)
    __table_args__ = (UniqueConstraint("company_id", "date", name="uq_snapshot_company_date"),)


class WatchlistItem(Base):
    """A watched company + its per-name valuation-alert configuration.

    Scoped by `user_key` so the same table cleanly supports multiple users once
    login lands — today everything defaults to a single 'default' owner. Carries
    `last_verdict`/`last_price` so verdict-upgrade and move alerts can be detected
    as transitions across reads rather than only as standing conditions."""
    __tablename__ = "watchlist_items"
    id          = Column(Integer, primary_key=True)
    user_key    = Column(String(64), nullable=False, index=True, default="default")
    company_id  = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    added_at    = Column(DateTime, server_default=func.now())
    # Per-name alert configuration
    target_price   = Column(Float, nullable=True)              # entry-band / target alert
    mos_threshold  = Column(Float, nullable=True, default=0.15)   # fire when MoS ≥ this
    move_threshold = Column(Float, nullable=True, default=0.08)   # fire on |1-day move| ≥ this
    alert_verdict  = Column(Integer, nullable=False, default=1)   # 1/0
    alert_mos      = Column(Integer, nullable=False, default=1)
    alert_target   = Column(Integer, nullable=False, default=1)
    alert_move     = Column(Integer, nullable=False, default=1)
    note           = Column(String, nullable=True)
    # Transition state (updated on each enriched read)
    last_verdict   = Column(String, nullable=True)
    last_price     = Column(Float, nullable=True)
    updated_at     = Column(DateTime, server_default=func.now(), onupdate=func.now())
    company        = relationship("Company")
    __table_args__ = (UniqueConstraint("user_key", "company_id", name="uq_watch_user_company"),)


class PortfolioHolding(Base):
    """A portfolio position (quantity + average cost) per company.

    Scoped by `user_key` exactly like WatchlistItem — everything defaults to a
    single 'default' owner until login lands. P&L / weights are computed at
    read time from MarketSnapshot + Valuation, never stored."""
    __tablename__ = "portfolio_holdings"
    id         = Column(Integer, primary_key=True)
    user_key   = Column(String(64), nullable=False, index=True, default="default")
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    qty        = Column(Float, nullable=False)
    avg_cost   = Column(Float, nullable=False)
    added_at   = Column(DateTime, server_default=func.now())
    company    = relationship("Company")
    __table_args__ = (UniqueConstraint("user_key", "company_id", name="uq_portfolio_user_company"),)


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


class CorporateAction(Base):
    """Dividends / splits / bonuses per company — the raw ledger behind
    total-return math (dividends) and the price back-adjustment engine
    (splits/bonuses). Parsed from IndianAPI `stockCorporateActionData` by the
    ingester; purged + re-inserted per company each refresh (idempotent).

    action_type : "dividend" | "split" | "bonus"
    ex_date     : "YYYY-MM-DD" — dividend xd-date / split xs-date / bonus xb-date
    value       : ₹/share cash dividend (None for split/bonus)
    ratio       : PRE-event price multiplier for split/bonus — multiply every
                  close STRICTLY BEFORE ex_date by this to put the series on the
                  current per-share basis. Split FV 2→1 ⇒ 0.5; bonus 4:1 ⇒ 0.2.
                  (None for dividends.)

    Additive table — auto-creates via Base.metadata.create_all; never ALTERs an
    existing table. NULL `value` on split/bonus rows is intentional; the ingester
    purges + de-dupes before insert so the unique constraint is only a safety net.
    """
    __tablename__ = "corporate_actions"
    id          = Column(Integer, primary_key=True)
    company_id  = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    action_type = Column(String(12), nullable=False)          # dividend | split | bonus
    ex_date     = Column(String(10), nullable=True, index=True)  # "YYYY-MM-DD"
    record_date = Column(String(10), nullable=True)
    value       = Column(Float, nullable=True)                # ₹/share (dividends)
    ratio       = Column(Float, nullable=True)                # pre-event price multiplier (split/bonus)
    raw_remarks = Column(String, nullable=True)
    source      = Column(String, default="indianapi")
    company     = relationship("Company")
    __table_args__ = (UniqueConstraint("company_id", "action_type", "ex_date", "value",
                                       name="uq_corp_action"),)


class ApiUsage(Base):
    """Durable monthly IndianAPI call counter — the vendor exposes no meter and
    the quota (~10k/mo on the dev plan) is a hard ceiling, so we count our own
    calls and let bulk jobs pre-flight against a budget before spending it.
    One row per calendar month ('YYYY-MM'); the scheduler records the delta after
    each ingest run. Additive table — auto-creates via create_all."""
    __tablename__ = "api_usage"
    id      = Column(Integer, primary_key=True)
    month   = Column(String(7), unique=True, nullable=False, index=True)   # "YYYY-MM"
    calls   = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AlphaSnapshot(Base):
    """Daily record of each name's multi-factor Alpha Score + rank — the raw
    ledger behind a future public 'factor track record' (does the top-Alpha
    decile actually out-perform?). Written by the scheduler after the daily
    recompute. History cannot be backfilled, so we start capturing now. One row
    per (company, date). Additive table."""
    __tablename__ = "alpha_snapshots"
    id          = Column(Integer, primary_key=True)
    company_id  = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    ticker      = Column(String(24), nullable=False, index=True)
    date        = Column(String(10), nullable=False, index=True)   # "YYYY-MM-DD"
    price       = Column(Float, nullable=True)
    alpha_score = Column(Float, nullable=True)
    rank        = Column(Integer, nullable=True)
    value       = Column(Float, nullable=True)
    quality     = Column(Float, nullable=True)
    momentum    = Column(Float, nullable=True)
    low_vol     = Column(Float, nullable=True)
    growth      = Column(Float, nullable=True)
    catalyst    = Column(Float, nullable=True)
    __table_args__ = (UniqueConstraint("company_id", "date", name="uq_alpha_company_date"),)


class ConsensusSnapshot(Base):
    """Daily record of analyst consensus (target / implied upside / rating) per
    company — the raw ledger behind estimate-REVISION signals (upgrades precede
    price drift). Can't be backfilled, so capture starts now. One row per
    (company, date). Additive table."""
    __tablename__ = "consensus_snapshots"
    id           = Column(Integer, primary_key=True)
    company_id   = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    ticker       = Column(String(24), nullable=False, index=True)
    date         = Column(String(10), nullable=False, index=True)   # "YYYY-MM-DD"
    target       = Column(Float, nullable=True)
    upside       = Column(Float, nullable=True)
    rating       = Column(String, nullable=True)
    num_analysts = Column(Float, nullable=True)
    __table_args__ = (UniqueConstraint("company_id", "date", name="uq_consensus_company_date"),)


class SavedScenario(Base):
    """A user's saved DCF slider state for a company — persist your what-if work
    and reload it later (the collaboration primitive proper terminals sell).
    Scoped by user_key like watchlist/portfolio; `data` is the assumptions dict.
    Additive table."""
    __tablename__ = "saved_scenarios"
    id         = Column(Integer, primary_key=True)
    user_key   = Column(String(64), nullable=False, index=True)
    ticker     = Column(String(24), nullable=False, index=True)
    name       = Column(String(120), nullable=False)
    data       = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, server_default=func.now())
    __table_args__ = (UniqueConstraint("user_key", "ticker", "name", name="uq_scenario"),)


class SavedScreen(Base):
    """A user's saved screener state (query / sector / sort — the `data` dict is
    schemaless so new filters serialize without a migration). Scoped by user_key
    like watchlist/portfolio. Additive table."""
    __tablename__ = "saved_screens"
    id         = Column(Integer, primary_key=True)
    user_key   = Column(String(64), nullable=False, index=True)
    name       = Column(String(120), nullable=False)
    data       = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, server_default=func.now())
    __table_args__ = (UniqueConstraint("user_key", "name", name="uq_screen"),)
