# EquityVerdict — Backend API

FastAPI service behind [equityverdict.com](https://equityverdict.com): an independent
equity-research terminal covering the **full Nifty 500** (~1,000 tracked names).
It stores normalized company financials and serves valuation, fundamentals,
technicals, and an explainable BUY / ACCUMULATE / HOLD / REDUCE / AVOID verdict.

Two things shape almost every design decision here:

- **Absent beats wrong.** A wrong number is never preferred over no number.
  Plausibility gates run on every published metric, statement line, ratio and
  date; when a figure cannot be trusted, the terminal says so instead of
  printing something confidently incorrect.
- **No AI.** The platform is 100% AI-free by owner mandate. Every opt-in LLM
  path was deliberately removed on 16 Jul 2026. Do not reintroduce an LLM call
  without explicit sign-off — `app/news_routes.py` and `app/transcript_nlp.py`
  carry tombstone comments where the old ones were.

## Run locally

Requires **Python 3.13** (CI pins it, and the codebase uses syntax older
versions cannot parse).

```bash
python3.13 -m venv venv313 && source venv313/bin/activate
pip install -r requirements.txt
export DATABASE_URL="sqlite:////tmp/equityverdict.db"
uvicorn app.main:app --reload            # http://127.0.0.1:8000
```

The schema is created on boot: `app/migrations_boot.py` stamps-or-upgrades
through Alembic, so a fresh database needs no separate migration step.

Interactive API docs at http://127.0.0.1:8000/docs — that is the authoritative
endpoint list. There are **26 routers**; any table in this file would be stale
within a week. Common entry points: `/api/health`, `/api/companies`,
`/api/companies/{ticker}`, `/api/universe`, `/api/quality/cross-check`.

> **`python -m app.seed` DROPS AND RECREATES EVERY TABLE** (`app/seed.py:67`).
> It loads a small illustrative universe from a deterministic PRNG, matching an
> old frontend fixture — it is not, and never was, production data. Run it only
> against a throwaway local database, never with `DATABASE_URL` pointing
> somewhere you care about. Production is populated by the ingesters.

### Tests

```bash
./venv313/bin/python -m pytest tests -q          # 112 test files
./venv313/bin/python tests/calibration_check.py  # valuation calibration gate
./scripts/sync_parity_fixtures.sh                # the three parity harnesses
```

## Layout

```
app/
  database.py         engine/session (SQLite locally, Postgres in production)
  models.py           ORM — Company, FinancialFact, HistoricalPrice, Valuation, …
  concepts.py         canonical chart of accounts (the normalization vocabulary)
  assemble.py         DB rows -> the flat dict the engines consume
  derive.py           assumption derivation (parity-locked with derive.js)
  engines.py          FCFF DCF + residual income + technicals + recommend
                      (parity-locked with engine.js)
  sector_params.py    per-sector anchors: terminal growth, mature ROIC, WACC
  data_quality.py     the trust layer — confidence score, level and flags
  data_integrity.py   the weekly standing integrity sweep
  corporate_events.py demergers / mergers / delistings and how they present
  ingest/             IndianAPI + Dhan ingesters, backfills, reclassification
  dhan/               broker client: instrument master, EOD prices, option chains
  main.py             FastAPI app; 26 routers mounted here
alembic/              migrations (5 revisions) — Alembic OWNS the schema
deploy/aws/           Dockerfile, deploy.sh, cutover.sh, ROLLBACK.md
```

## Parity contracts

Three harnesses lock this service to the React frontend so the two agree
exactly rather than approximately. All three must pass before a push, and the
pre-push hook regenerates them when the locked math changes:

| contract | cases |
|---|---|
| `app/engines.py` ↔ `src/lib/engine.js` | 60 |
| `app/derive.py` ↔ `src/lib/derive.js` | 48 |
| verdict ladder ↔ `src/lib/recommend.js` | 113 |

Editing `engines.py`, `derive.py`, `sector_params.py` or `data_quality.py`
changes committed fixtures. That is expected — but read the diff, because an
unintended fixture change is exactly how a silent valuation regression ships.

## Data sources

Two vendors, deliberately independent and cross-checked against each other:

- **IndianAPI** — fundamentals, statements, corporate actions, profiles. Served
  from the Developer plan's **dedicated** host, `dev.indianapi.in`. The shared
  `stock.indianapi.in` does not reach this plan: it answers 429 forever while
  the vendor console shows zero usage. *If vendor data looks dead, check the
  HOST before the quota* — that misreading cost nine days in Aug 2026.
- **Dhan** — EOD and intraday prices, the NSE instrument master, option chains.
  Token renewal is automated via TOTP.

Spend is metered centrally (`app/vendor_meter.py`) and capped against a monthly
budget (`app/api_budget.py`). The billing cycle is **not** the calendar month:
it starts on `INDIANAPI_CYCLE_DAY`, which defaults to 1 in code but is set to
**11** in production. Read the live value before reasoning about a reset date.

## Production

AWS Mumbai (`ap-south-1`): one EC2 box running `caddy` → `web` and `scheduler`
containers, RDS Postgres 16, ECR for images, Cloudflare R2 for documents and
encrypted backups. Frontend on Vercel.

**Deploys are manual**: `./deploy/aws/deploy.sh` builds, pushes, cuts over via
SSM, and proves success by comparing image digests rather than trusting a health
check — a green `/api/health` can be old code still serving.

`main` is protected; `backend-tests`, `backend-tests-postgres` and
`backend-image-boots` must pass before merge.

## Where to look next

- **`HANDOFF.md`** — read this first. Live system map, deploy mechanics, what is
  known-open, and the traps that have actually bitten.
- **`ARCHITECTURE.md`** — as-built topology and the valuation pipeline.
- **`COMPLIANCE.md`** — regulatory position. **The platform is not yet cleared to
  charge users**: SEBI research-analyst registration and data-redistribution
  licensing are both still open.
