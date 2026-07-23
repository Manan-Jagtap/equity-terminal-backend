# Disaster-recovery drill (FIX-27 / OPS-05)

The restore path (R2 ciphertext → decrypt → RDS) round-trips in unit tests and
was proven once for real on 18 Jul — which surfaced **three latent DR bugs**
(R2 key casing, pg8000 TLS, Postgres sequence resync) that unit tests could not.
Drills find what tests can't, so run one **quarterly**.

## Quarterly restore drill
Restore the latest daily backup into a **scratch** database (never prod) and
check row counts against the manifest.

```bash
# 1. scratch DB (local Postgres or a throwaway RDS)
export SCRATCH_DB="postgresql+pg8000://postgres:postgres@localhost:5432/dr_scratch"

# 2. restore the chosen date's ciphertext from R2
DATABASE_URL="$SCRATCH_DB" python scripts/restore_backup.py <YYYY-MM-DD>

# 3. sanity: key tables are populated and counts are plausible vs the manifest
DATABASE_URL="$SCRATCH_DB" python -c "
from app.database import SessionLocal; from app import models
s=SessionLocal()
for m in (models.Company, models.Valuation, models.HistoricalPrice, models.User):
    print(m.__tablename__, s.query(m).count())
"
```
Archive the output (date + counts) so drift is visible drill-over-drill.
Verification: counts match the backup manifest; no restore error.

## RDS point-in-time recovery (PITR)
Single instance, no replica — PITR is the finer-grained safety net between daily
dumps. **Confirm in the RDS console** (one-time, near-free at this size):
`Automated backups` retention **> 0 days** and a visible PITR window. Record the
retention here once verified:

- RDS automated-backup retention: **_(fill in: e.g. 7 days)_**
- PITR window confirmed: **_(date checked)_**

## Moving storage off R2 (S3 or other S3-compatible host)
`R2_ENDPOINT` now overrides the endpoint (FIX-27), so the backend can point at
S3 without a code change:

```
# unset to use Cloudflare R2 (default); set to move:
R2_ENDPOINT=https://s3.ap-south-1.amazonaws.com
R2_ACCESS_KEY_ID=...      R2_SECRET_ACCESS_KEY=...      R2_BUCKET=...
```
`R2_ACCOUNT_ID` is still required for the default R2 URL; with `R2_ENDPOINT` set
it is unused for the endpoint. Rehearse a backup+restore against the new host
before switching prod.


## Drill log

### 2026-07-23 — first drill: PASSED
- Backup: `backups/2026-07-23/` (26 table files; backups are running DAILY).
- Restored 25 tables into a scratch `dr_drill` database on the prod RDS
  instance (created + dropped in-drill): 458k+ rows including
  historical_financials 277,915 · verdict_snapshots 22,467 · price_points
  122,503 · all 998 companies / 997 valuations / 7 users. Every table's
  count verified against prod within 2% drift tolerance.
- `historical_prices` (1,058,986 rows) was decrypt+parse-verified but NOT
  DB-loaded: the prod box has 2GB RAM and a full in-place restore swap-crawls
  while competing with live traffic (the first attempt proved it).
- LESSONS baked into procedure:
  1. Full restores never run on the prod box — quarterly rehearsals restore
     to a laptop/scratch host (`scripts/restore_backup.py` with the R2 creds
     + BACKUP_KEY from /opt/app.env).
  2. `scripts/` now ships in the Docker image so DR tooling exists wherever
     the app runs.
  3. Detached one-offs must log per-step (a killed `docker exec -d` discards
     stdout — a silent phase looks identical to a hang).
