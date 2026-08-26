# System Map — EquityVerdict (Phase 1 recon, 2026-07-19)

## Corrected reality vs the audit brief (the brief was written in the June Railway/yfinance/Claude era)
| Brief said | Actual (verified) |
|---|---|
| Backend on **Railway** | **AWS Mumbai** (ap-south-1). Railway compute deleted; Railway Postgres kept ~1wk as rollback only |
| Data from **yfinance** | **Dhan** (prices) + **IndianAPI** (fundamentals). yfinance mostly migrated off; ONE live path (intraday spot, indianapi_ingester.py:~1295); pinned yfinance==0.2.51 |
| **AI-generated thesis (Claude)** | **AI-free.** thesis_routes.py = retired stub; news/transcript de-AI'd. Residual dormant LLM path scheduler.py:530-543 gated on ANTHROPIC_API_KEY (unset) |
| "~500 NSE companies" | Universe tier is **top1000** (UNIVERSE_TIER=top1000); ~500 visible/covered |
| 123 endpoints, ~90 modules | Confirmed: **123 endpoints**, **86 app/ modules**, ~30 routers, **40 frontend components** |

## Infrastructure (AWS ap-south-1 Mumbai)
- **EC2 t3.small** i-0f60f2dd6fc5fabd5 (resized from t3.micro after a swap-death outage), Elastic IP **3.6.183.42**. 3 Docker containers on network `edge`: `caddy` (auto-TLS reverse proxy for api.equityverdict.com, `encode zstd gzip`, HSTS, → web:8080), `web` (FastAPI/uvicorn single worker, --env-file /opt/app.env), `scheduler` (python scheduler.py). All --restart always.
- **RDS Postgres 16.14** equity-terminal-db (postgresql+pg8000, SSL). Single instance, no replica.
- **ECR** 593334122677.dkr.ecr.ap-south-1.amazonaws.com/equity-terminal:latest. **S3** config bucket (ec2.env). **R2** (Cloudflare, outside India) for encrypted backups.
- **Frontend**: Vercel (global CDN, static React build). DNS at GoDaddy: A @→Vercel(76.76.21.21), CNAME www→Vercel, A api→3.6.183.42. Mail: Titan (secureserver.net).
- Deploy path: image rebuild → ECR push → owner runs `aws ssm send-command --cli-input-json file://~/.equity-terminal/cutover-cmd.json` (re-pulls ec2.env from S3, restarts web+scheduler+caddy). Backend `git push` deploys NOWHERE.

## Backend routers (30) → prefixes
history(/api/companies), news(/api/companies), bse(/api/bse), market(/api/market), profile(/api/companies), watchlist(/api/watchlist), compare(/api), results(/api), ownership(/api), operations(/api), logo(/api), backtest(/api), export(/api/export), portfolio(/api/portfolio), scenario(/api/scenarios), dhan(/api), documents(/api), thesis(/api — retired stub), auth(/api/auth), quality(/api/quality), screens(/api/screens), admin(/api/admin), ipo(/api), mf(/api/mutual-funds), intraday(/api/companies), macro(/api/macro). Plus inline routes in main.py (health, company detail, valuation, onepager).

## DB schema (26 tables, app/models.py)
users, auth_events, companies, financial_facts, historical_financials, price_points, historical_prices, assumptions, market_snapshots, kv_store, company_insights, valuations, verdict_snapshots, watchlist_items, portfolio_holdings, quarterly_documents, corporate_actions, api_usage, alpha_snapshots, consensus_snapshots, saved_scenarios, saved_screens, engine_calls, transcript_insights (+ pending_signup lives in kv_store, not its own table).

## Scheduler jobs (scheduler.py, `schedule` lib, IST-ish times)
- Weekday 10:15 run_prices · 11:15 run_manager_evidence · 10:45 run_missing_history_backfill
- Daily 01:00 run_transcript_ingest · 02:00 run_regulatory_refresh · 02:30 run_nse_flows · 20:30 run_coverage_backfill
- Every 90 min run_intraday_prices
- Sunday 00:30 run_full · 23:30 run_macro_refresh · 03:00 run_data_integrity · 04:00 run_encrypted_backup
- Friday 21:00 _monthly_manager_calibration · 21:30 _monthly_universe_refresh · 22:30 run_results_calendar

## Frontend
- React 19.2 + Vite 8, hash-routed SPA. Views (VIEW_IDS): dashboard, screener, ideas, baskets, watchlist, portfolio, compare, economy, sectors, ipo, funds, manager, track + company pages (#/company/TICKER).
- Deps: lightweight-charts ^5.2, recharts ^3.8, pdfjs-dist ^4.10.38, lucide-react ^1.17. Dev: playwright, eslint 10, vite 8.
- Shared math mirrored from backend: src/lib/engine.js↔app/engines.py (parity 60/60), derive.js↔derive.py (parity 48/48). valuation.js collapsed into engine.js.
- **Known dead code:** /Users/manan_jagtap/equity-terminal/backend/{main.py,thesis.py} (~27KB, Jun 4) — stale duplicate of the real backend, owner wants removed.

## Data vendors
- **Dhan**: live LTP batch (500 eq + 11 indices, 12s cache, app/live_prices.py), 5y OHLCV, options, TOTP self-mint (app/dhan/auth.py). Personal-use licence (see CMP-02).
- **IndianAPI**: statements/profiles/ownership/docs/news; Growth plan, budget-guarded (app/api_budget.py, ~50k/mo, snapshot-first 7-day TTL). Base https://stock.indianapi.in.
  - *Correction appended 25 Aug 2026 — the line above records the July state and is left as found.* The plan is now **Developer**, and its base is that plan's DEDICATED host **https://dev.indianapi.in**. `stock.indianapi.in` is the SHARED host and was never right for this key: traffic to it never reaches the plan, which produced nine days of 429s in Aug 2026 while the vendor console showed 0 requests against a full 10,000.
- Live budget snapshot (2026-07-19): IndianAPI internal counter 11,582/50,000 after today's backfill (healthy).

## Auth model
Homegrown PBKDF2 (260k) password hash + HMAC-SHA256 signed stateless token (uid,email,exp; 30-day). AUTH_SECRET set in prod (64 chars). Admin gating = ADMIN_EMAILS via require_admin(). Email-ownership verification live (mailer.py + auth_routes.py: signup→emailed 6-digit code→/verify creates User; 5-attempt lockout; GoDaddy Titan SMTP smtpout.secureserver.net:465).

## Test posture
Backend pytest (needs venv313 + fresh sqlite DATABASE_URL + RATE_LIMIT_AUTH=100 for auth suites; ~209 passing pre-audit). Parity harnesses: tests/gen_parity_cases.py→engineParity.mjs (60/60), deriveParity (48/48). Frontend: Playwright e2e smoke (5 specs) + eslint at baseline. CI: GitHub Actions both repos + uptime.yml probing /api/health every 30 min.
