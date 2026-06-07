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

## 2. Migrate + repopulate the live DB — SERVER-SIDE on Railway (no laptop)
The new `valuations` table is created automatically on boot. The full migration
(reclassify → ingest → precompute) now runs entirely on Railway via the
**scheduler service** — `railway run …` would execute on your laptop, which we
don't want.

One-time bootstrap:
1. Make sure the scheduler-service code is up to date: commit + push `scheduler.py`
   (it deploys from the same repo):
   ```bash
   cd ~/Downloads/backend
   git add scheduler.py DEPLOY_NOTES.md
   git commit -m "Scheduler: server-side bootstrap (reclassify+ingest+compute) + auto-recompute"
   git push origin main
   ```
2. In Railway → **equity-terminal-scheduler** service → **Variables**, add:
   ```
   RUN_BOOTSTRAP_NOW = true
   ```
   then **Redeploy** the service.
3. Watch the service **Logs**. You'll see, server-side:
   `BOOTSTRAP step 1/3 reclassify` → `2/3 full ingest` → `3/3 compute` → `BOOTSTRAP complete.`
4. **Remove** the `RUN_BOOTSTRAP_NOW` variable so it doesn't re-run on every restart.

After this, nothing manual is needed: the scheduler **auto-recomputes valuations**
on every daily price refresh and weekly full refresh.

> Prereqs on the scheduler service Variables: `INDIANAPI_KEY` and `DATABASE_URL`
> (the same Postgres the web service uses). It already has these if the weekly
> refresh was working before.
>
> No scheduler service yet? Create one in the Railway project from the same repo
> with start command `python scheduler.py`, give it `INDIANAPI_KEY` +
> `DATABASE_URL`, then do steps 2–4.

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
