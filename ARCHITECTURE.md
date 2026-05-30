# Equity Research Terminal — Architecture

> Living document. Reflects the codebase as of May 2026. Update it when the system changes.
> The point of this file: anyone (you, a future collaborator, or an AI assistant in a fresh session)
> should be able to read it and understand what exists, why, and what comes next — without
> re-deriving it from the code.

---

## 1. Mission

An equity research terminal for Indian markets. Type a ticker, get a complete, trustworthy
picture: financials, sector-correct valuation, asset quality, peer comparison, the latest
quarter's story, and a defensible buy/hold/avoid view — every number traceable to the filing
it came from.

### The realistic version of "finest in the world"

Bloomberg / FactSet / CapIQ are 30-year products with thousands of engineers and eight-figure
annual data licenses. We do not out-feature them on breadth. They are beatable on **focus and
judgment**: a terminal that deeply understands Indian NBFCs and banks — sector-correct
valuation, rigorous number verification, analyst-grade one-pagers — is something the giants do
generically and we can do excellently. Narrow and excellent beats broad and generic.

### The one rule that governs everything

**"Accurate enough for serious research."** A terminal with beautiful UI and wrong numbers is
worse than useless — it looks authoritative while being dangerous. Accuracy is not a feature;
it is the spine of the architecture (see §5).

---

## 2. System map

```
┌──────────────────────────────────────────────────────────────────┐
│  PRESENTATION    React + Vite (Vercel)                             │
│  App.jsx · Company.jsx · components/ · lib/                        │
│  company page · financials · valuation · ratios · one-pager        │
│  viewer · screener · peer comparison · charts                      │
└────────────────────────────┬───────────────────────────────────── ┘
                             │  REST / JSON
┌────────────────────────────┴───────────────────────────────────── ┐
│  API LAYER       FastAPI (Railway)  —  app/main.py                  │
│  /api/companies · /{ticker}/financials · /{ticker}/history ·        │
│  /valuation · /{ticker}/news · /onepager · /api/bse/*               │
└────────────────────────────┬───────────────────────────────────── ┘
        ┌───────────────┬─────┴────────┬─────────────────┬──────────┐
        │               │              │                 │          │
┌───────┴──────┐ ┌──────┴──────┐ ┌─────┴───────┐ ┌───────┴──────┐ ┌─┴────────┐
│ COMPUTATION  │ │INTELLIGENCE │ │ NORMALIZE   │ │ STORAGE      │ │ INGESTION│
│ engines.py   │ │ thesis.py   │ │ templates.py│ │ Postgres     │ │ ingest/  │
│ metrics.py   │ │ onepager.py │ │ concepts.py │ │ (models.py)  │ │ bse/     │
│ financials.py│ │ (LLM calls) │ │ assemble.py │ │ R2 (r2/)     │ │          │
└──────────────┘ └─────────────┘ └─────────────┘ └──────┬───────┘ └────┬─────┘
                                                         │              │
                          ┌──────────────────────────────┴──────────────┴───┐
                          │  DATA SOURCES                                     │
                          │  BSE/NSE XBRL filings · investor presentations ·  │
                          │  concall transcripts · price data · corp actions  │
                          └────────────────────────────────────────────────── ┘
```

---

## 3. Current codebase — what each module does

### API layer
| File | Role |
|------|------|
| `main.py` | FastAPI app. Routes: `/api/companies`, `/api/companies/{ticker}`, `/valuation`, `/onepager`, `/api/health`. Registers `history_router`, `news_router`, `bse_router`. |
| `history_routes.py` | `GET /{ticker}/history` (price history), `GET /{ticker}/financials` (sector-aware P&L). |
| `news_routes.py` | `GET /{ticker}/news`. **LLM source currently paused** via `NEWS_LLM_ENABLED` flag (cost control). Falls back to marketaux + yfinance. |
| `bse_routes.py` | `GET /api/bse/announcements/{ticker}`, `POST /api/bse/fetch/{ticker}`. Pulls IP + transcript from BSE → R2. |

### Computation
| File | Role |
|------|------|
| `engines.py` | Valuation engines. Ported 1:1 from frontend so both agree exactly. |
| `metrics.py` | Ratio / metric calculations. |
| `financials.py` | Template-aware P&L formatter. NBFC → NII shape; manufacturer → revenue/EBITDA shape. |
| `assemble.py` | `build_company`, `assumptions_dict` — assembles the company payload the API returns. |

### Normalization
| File | Role |
|------|------|
| `templates.py` | Sector classifier. 8 template codes: BANK, NBFC, INSURANCE, IT_SERVICES, MANUFACTURING, CONSUMER, PHARMA, ENERGY. Drives P&L shape + which ratios appear. |
| `concepts.py` | Financial concept definitions (imported as `K`). |

### Intelligence (LLM)
| File | Role |
|------|------|
| `thesis.py` | Buy/hold/avoid thesis generator. Uses Claude Sonnet. |
| `onepager.py` | Current one-pager (`build_onepager`). The basic version — being replaced by the premium sector-template flow. |

### Storage
| File | Role |
|------|------|
| `models.py` | SQLAlchemy models. `Company` (+ `template_code`, `bse_scrip_code`), `FinancialFact`, `HistoricalFinancial`, `HistoricalPrice`, `Assumptions`, `MarketSnapshot`, `PricePoint`, `QuarterlyDocument` (BSE docs). |
| `database.py` | Engine, `Base`, `get_db`, `SessionLocal`. |
| `schemas.py` | Pydantic request/response models. |
| `seed.py` | DB seeding. |
| `r2/` | Cloudflare R2 client (boto3). Stores IP + transcript PDFs. Key pattern: `companies/{ticker}/{quarter}/{doc_type}.pdf`. |

### Ingestion
| File | Role |
|------|------|
| `ingest/xbrl_ingester.py` | Pulls XBRL financial filings (authoritative numbers). |
| `ingest/price_ingester.py` | Price/OHLCV data. |
| `ingest/fundamentals_ingester.py` | Fundamentals. |
| `ingest/bse_results_ingester.py` | BSE results data. |
| `ingest/bulk_ingester.py` | Bulk company onboarding. |
| `ingest/run_all.py` | Orchestrates the ingesters. |
| `bse/` | Document fetcher (client, classifier, scrip_codes, fetcher) — pulls IP + transcript PDFs from BSE corporate announcements. |

### ⚠ Known tech debt
- **Duplicate ingesters**: `app/bse_results_ingester.py`, `app/fundamentals_ingester.py`, `app/price_ingester.py`, `app/run_all.py` appear to be stale copies of the `app/ingest/` versions. Confirm which are imported, delete the dead ones.
- **`QuarterlyDocument.bse_filing_date` is NULL**: BSE's `DT_TM` format with fractional seconds (`2026-05-20T16:29:09.75`) isn't parsed. Fix in `bse/client.py:parse_filing_date`.
- **yfinance reliability**: convenient for a live tick, unreliable for the financials a valuation depends on. Treat as last-resort tier, never authoritative.

---

## 4. Target architecture by layer

### Ingestion (the foundation, and the hardest)
Source trust hierarchy:
1. **Company XBRL filings (BSE/NSE)** — authoritative. Messy: taxonomies shift, companies file inconsistently. This is where accuracy is won or lost.
2. **Investor presentations + concall transcripts** — now flowing via `bse/`. Narrative + management guidance + segment detail.
3. **Price / market data** — yfinance convenient but unreliable for Indian tickers (stale prices, wrong splits). Needs a better tier eventually.

The job isn't just to *pull* — it's pull → **validate** → **normalize** → **store with provenance**.

### Storage
- **Postgres**: structured numbers (companies, financials, prices, snapshots, doc metadata).
- **R2**: source PDFs (IP, transcript). Every computed number should point back to its source filing.

### Normalization
The `template_code` system. More load-bearing than it looks — it routes every company to the right P&L shape, the right ratios, and (critically) the right valuation model.

### Computation — the sector-correct valuation rule
**You cannot DCF a bank or NBFC the way you DCF a manufacturer.** For a lender, debt is raw
material, not financing — free-cash-flow DCF is meaningless. Financials need:
- Residual income / excess-return-on-equity models, OR
- P/B-vs-ROE regression, OR
- Dividend discount / Gordon growth on sustainable ROE.

Manufacturers/consumer/pharma/energy use FCFF DCF + EV/EBITDA + P/E multiples. The template
system routes each company to the correct engine. **This is where amateur terminals get it
embarrassingly wrong** — and where your NBFC expertise is the edge.

### Intelligence
LLM sits **on top of** verified data. It narrates and synthesizes; the numbers it uses come
from the validated store, not from the model's own reading of a PDF. That separation is what
keeps it trustworthy. Three jobs: extract structured data from IP/transcript → render the
one-pager → write the thesis.

---

## 5. The data-accuracy spine

Four principles, baked in — not bolted on:

1. **Authoritative source, always.** Numbers from XBRL filings, not scraped aggregators.
   yfinance is fine for a price tick; not for financials you value a company on.

2. **Provenance on every number.** Each stored figure carries where it came from — which
   filing, which line item, what date. When a number looks wrong, trace it in one click.
   *This is literally what separates an institutional tool from a hobby project.*

3. **Validation, not blind trust.** Compute the same thing multiple ways and flag mismatches:
   - Does the balance sheet balance?
   - Does PAT reconcile P&L → cash-flow opening line?
   - Does AUM × yield ≈ interest income?
   When these don't tie out, the terminal **says so** rather than silently showing a wrong
   number. (You already do this manually on one-pagers — we systematize it.)

4. **Human override with audit.** When automated parsing gets something wrong (it will), you
   correct it, and the correction is logged. The system gets more accurate over time instead
   of repeating the same error.

Get this layer right and "accurate enough for serious research" is achievable on free sources.
Skip it and no amount of UI polish saves it.

---

## 6. Build philosophy — vertical slices, NBFC-first

Do **not** build each horizontal layer fully before connecting anything. Build thin vertical
slices that exercise every layer end-to-end, get them correct for NBFCs, then widen.

The one-pager work is exactly this: the first vertical slice through the Intelligence layer.
It forced real chunks of Ingestion (BSE fetcher) and Storage (R2 + QuarterlyDocument) into
existence along the way. That's the right pattern.

**Scope discipline > ambition.** The fastest way to kill this project is to chase breadth.
NBFCs end-to-end and excellent, then expand to banks, then the rest.

---

## 7. Roadmap

### Done ✓
- FastAPI + Postgres + Vercel skeleton
- Sector templates (`template_code`) + classifier
- Sector-aware financials endpoint
- Valuation engines (ported from frontend)
- XBRL / price / fundamentals ingesters
- News (paused for cost via `NEWS_LLM_ENABLED`)
- Thesis generator
- **B2: BSE document fetcher → R2** (IP + transcript, idempotent, verified on Muthoot Q4FY26)

### In progress — the premium one-pager vertical slice
- **B3** — Backfill: all NBFCs × last N quarters of IP + transcript into R2.
- **B4** — Extraction: IP + transcript PDFs → structured JSON via Claude (numbers verified against the financials store, not taken from the model's reading).
- **B5** — Render: sector-specific HTML templates (NBFC first) → Playwright → A4 PDF. Brand-color matched to the deck.
- **B6** — Frontend: "Generate Premium One-Pager" button with quarter dropdown (auto-detect latest).

### Next slices (sequenced, not parallel)
- **Accuracy spine v1**: provenance columns + validation checks (balance-sheet tie-out, PAT reconciliation, AUM×yield sanity) with a "numbers don't tie" flag in the UI.
- **Sector-correct valuation v1**: route NBFC/BANK to residual-income / P-B-vs-ROE; keep FCFF for non-financials. Surface the model choice in the UI so it's never a black box.
- **Peer comparison**: NBFC vs NBFC on the metrics that matter (NIM, ROA, ROE, GNPA, C/I, AUM growth, CRAR).
- **Screener**: filter the NBFC universe on those same metrics.
- **Human override + audit**: editable numbers with a change log.
- **Tech-debt cleanup**: delete duplicate ingesters; fix `bse_filing_date` parsing.

### Later (deliberately deferred)
- Bank template end-to-end (after NBFC is excellent)
- Remaining sectors (IT, pharma, consumer, energy)
- Better price-data tier than yfinance
- Alerting on new filings (auto-generate one-pager when a result drops)

---

## 8. Operating constraints

- **Build/deploy loop**: code is written and sandbox-tested, then applied through your terminal.
  You own all credentials and dashboards (Railway, Cloudflare, GitHub). This is correct for
  something handling production financial data — secrets never pass through chat.
- **Data accuracy is continuous**, not a milestone. Indian filings are messy; we'll be fixing
  parsing edge cases for as long as this project lives. Budget for it.
- **Cost discipline**: LLM calls cost money (the reason news is paused). Every LLM feature gets
  a flag and a cost estimate before it ships.

---

## 9. Environment / infra reference

| Thing | Where |
|-------|-------|
| Backend | Railway — `equity-terminal-backend` service |
| Database | Railway Postgres (`DATABASE_URL`, `DATABASE_PUBLIC_URL`) |
| Doc storage | Cloudflare R2 — bucket `equity-terminal-docs` |
| Frontend | Vercel (React/Vite) |
| Repo (backend) | `github.com/Manan-Jagtap/equity-terminal-backend` |
| Repo (frontend) | `github.com/Manan-Jagtap/equity-terminal` |
| LLM | Anthropic API (`ANTHROPIC_API_KEY`) — thesis + (paused) news + (coming) one-pager |

### Env vars (Railway backend)
```
DATABASE_URL              # Postgres (internal)
ANTHROPIC_API_KEY         # Claude
MARKETAUX_API_KEY         # news fallback
NEWS_LLM_ENABLED          # false = news LLM paused (cost control)
R2_ACCOUNT_ID             # 32-char hex ONLY (not the full endpoint URL)
R2_ACCESS_KEY_ID
R2_SECRET_ACCESS_KEY
R2_BUCKET                 # equity-terminal-docs
```

---

*Keep this file honest. If the code and this document disagree, one of them is a bug.*
