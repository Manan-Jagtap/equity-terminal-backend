# Equity Research Terminal — Backend API

FastAPI service that stores normalized company financials and serves live
valuation, fundamentals, technicals and an explainable buy/hold/avoid verdict.
The engines are ported 1:1 from the React frontend, so both agree exactly.

## Run locally (5 commands)

```bash
cd backend
python -m venv venv && source venv/bin/activate     # Windows: venv\Scripts\activate
pip install -r requirements.txt
python -m app.seed                                   # build + seed the database
uvicorn app.main:app --reload                        # serve at http://127.0.0.1:8000
```

Open http://127.0.0.1:8000/docs for interactive API docs.

## Endpoints
| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | liveness check |
| GET | `/api/companies` | screener rows (verdict, score, intrinsic) |
| GET | `/api/companies/{ticker}` | full detail for one company |
| POST | `/api/companies/{ticker}/valuation` | recompute with tweaked assumptions |

## Structure
```
app/
  database.py   engine/session (SQLite local, Postgres in prod)
  concepts.py   canonical chart of accounts (the normalization vocabulary)
  models.py     ORM: Company, FinancialFact (time-series), Assumptions, prices
  assemble.py   turns DB rows into the flat dict the engines consume
  engines.py    residual-income + FCFF + fundamentals + technicals + recommend
  schemas.py    request body for assumption overrides
  seed.py       sample universe (replace with real / XBRL-ingested data)
  main.py       FastAPI app + endpoints + CORS
```

## Making it real
Replace the seed numbers with ingested data. The clean path:
1. Pull quarterly **XBRL** from BSE/NSE → map each tag to a code in `concepts.py`
   → insert `FinancialFact` rows (source="xbrl").
2. Pull OHLC from a market-data API → replace `PricePoint` rows.
3. Extract annual-report/deck tables (Textract/Camelot) → LLM-to-JSON →
   **reconcile against the XBRL value** before inserting.

Nothing in `engines.py` changes when you swap the data source — that's the point
of the `assemble.py` seam.
