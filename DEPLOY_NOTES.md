# Deploy notes — independent-DCF rebuild (June 2026)

All code is written, compiles, and passes the test suite (`python -m pytest tests/ -q`).
Three things must run on YOUR side (the sandbox can't reach git or Railway):

## 1. Commit & push (you own the credentials)
```bash
cd ~/Downloads/backend
rm -f .git/index.lock
git add app/ tests/ .gitignore AUDIT_2026-06.md DEPLOY_NOTES.md
git commit -m "Independent DCF: history-derived assumptions, sector betas, bank fix, validation, tests"
git push origin main
```
(No inline `#` comments — zsh does not strip them and will treat them as git arguments. Railway auto-deploys the backend on push.)
Optional, to stop shipping the backup/scratch files (now in .gitignore):
```bash
git rm -r --cached --ignore-unmatch '*.bak' '*.bak.*' 'app/**/*.bak.*' \
  'app/ingest/probe_*.py' 'app/ingest/*_dump.json' '*.db' .DS_Store 'app/.DS_Store'
git commit -m "Stop tracking backups, probe dumps, local DBs, .DS_Store"
git push
```

## 2. Migrate + repopulate the live DB (Railway)
The new `valuations` table is created automatically on boot (`Base.metadata.create_all`).
Then, ONCE, run these against the live Postgres so the data is correct:
Run these one at a time (no inline comments). Order matters: a → b → c.
```bash
railway run python -m app.ingest.reclassify
railway run python -m app.ingest.indianapi_ingester --nifty50
railway run python -m app.ingest.compute_valuations
```
- (a) `reclassify` fixes templates (HDFC Bank → BANK, etc.).
- (b) `indianapi_ingester --nifty50` re-pulls statements so the bank/NBFC P&L gets NII / interest / provisions (uses your INDIANAPI_KEY already on Railway).
- (c) `compute_valuations` precomputes the independent valuations so /api/companies is instant.

Re-run (c) after any price refresh; the weekly scheduler already refreshes (a/b) data, and you can add (c) to it later.

## 3. Verify
```bash
curl -s https://equity-terminal-backend-production.up.railway.app/api/companies/TCS \
  | python -m json.tool | grep -E 'intrinsic|verdict|method'
# expect: FCFF DCF, intrinsic within ~30% of price, verdict HOLD/ACCUMULATE (no TRIM)

curl -s https://equity-terminal-backend-production.up.railway.app/api/companies/HDFCBANK/financials \
  | python -m json.tool | grep -E 'template|roe'
# expect: template BANK, roe ~0.13–0.16 (NOT 0.49–0.92)
```

## Notes
- Set `FRONTEND_ORIGIN` on Railway to your Vercel URL to lock CORS (currently `*`).
- `app/thesis.py`, `app/bse_results_ingester.py` (top-level dup) are unused/dead but
  left in place to avoid surprises — safe to delete when you want.
- The model is now **independent**: it can and will disagree with analyst consensus
  (e.g. it may flag a richly-priced bank as REDUCE while the street says BUY). The
  consensus is returned separately in every response (`analyst` block) so both views
  show side by side — never blended into the intrinsic.
