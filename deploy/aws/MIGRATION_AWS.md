# AWS Migration Runbook — Railway → AWS Mumbai (`ap-south-1`)

*Written for a non-technical operator: every step is a console click or a single
pasted command. Do the steps in order; each phase ends with a check. Nothing
here touches Railway until the final cutover, so the live site keeps running
throughout.*

**Why Mumbai matters to you:** Railway has no India region — today your user
data sits in US/EU data centres. Moving everything to `ap-south-1` puts the
platform's data physically in India, which is what your DPDP posture actually
requires. **Every resource below must be created with the region set to
`Asia Pacific (Mumbai) ap-south-1` — check the region picker (top-right of the
AWS console) before every step.**

**Target shape** (as in ARCHITECTURE.md):
- **App Runner** → the FastAPI web service (built from `deploy/aws/Dockerfile`)
- **ECS Fargate (1 task)** → the scheduler (same image, command `python scheduler.py`)
- **RDS PostgreSQL** → the database
- **S3 (Mumbai)** → replaces Cloudflare R2 for PDFs + encrypted backups
- **Amplify Hosting** → the React frontend (replaces Vercel)

**Monthly cost ballpark:** RDS `db.t4g.micro` ~$15 · App Runner ~$5–15 ·
Fargate 0.25 vCPU task ~$10 · S3 + ECR a few $ · Amplify ~$1. Roughly **$30–45/mo.**

---

## Phase 0 — before you start (30 min, no risk)

1. Create the AWS account at aws.amazon.com (your card; enable MFA on the root
   user when prompted — say yes).
2. In the console, set the region picker (top-right) to **Asia Pacific (Mumbai)**.
3. Make sure a recent **encrypted backup exists**: the scheduler takes one every
   Sunday 04:00 UTC. Its log line looks like `encrypted backup: {'status': 'ok',
   'date': '2026-07-…'}` (Railway → scheduler service → Logs). Note the date —
   this backup is your data-moving vehicle.
4. Confirm `BACKUP_KEY` is set on the Railway scheduler service and that you
   have the same passphrase saved somewhere safe. Without it the backup cannot
   be decrypted — by design.

## Phase 1 — build & push the container image (45 min)

On your Mac, with Docker Desktop installed and running:

```bash
cd ~/Downloads/backend
# one-time: install the AWS CLI, then sign in (choose ap-south-1 as default region)
brew install awscli
aws configure          # paste an access key created in AWS Console → IAM → your user → Security credentials

# create the image registry and push
aws ecr create-repository --repository-name equity-terminal --region ap-south-1
aws ecr get-login-password --region ap-south-1 | docker login --username AWS \
  --password-stdin $(aws sts get-caller-identity --query Account --output text).dkr.ecr.ap-south-1.amazonaws.com
docker build -f deploy/aws/Dockerfile -t equity-terminal .
docker tag equity-terminal:latest \
  $(aws sts get-caller-identity --query Account --output text).dkr.ecr.ap-south-1.amazonaws.com/equity-terminal:latest
docker push $(aws sts get-caller-identity --query Account --output text).dkr.ecr.ap-south-1.amazonaws.com/equity-terminal:latest
```

*(Optional rehearsal first: `docker compose -f deploy/aws/docker-compose.yml up
--build`, then `curl localhost:8080/api/health` → `{"status":"ok",…}`.)*

## Phase 2 — database (RDS Mumbai, ~30 min + 10 min creation wait)

1. Console → **RDS** → Create database → *Standard create* → **PostgreSQL**.
2. Template **Free tier** (or `db.t4g.micro`), name `equity-terminal-db`,
   set a master password (save it), storage 20 GB gp3.
3. Public access: **Yes** for the migration day (we lock it down in Phase 6).
4. Create, wait for *Available*, copy the **Endpoint** hostname.
5. Check: from your Mac,
   `psql postgresql://postgres:<password>@<endpoint>:5432/postgres -c "select 1"`
   (or use any DB client). A `1` back means the database is reachable.

## Phase 3 — move the data (the cutover vehicle, ~20 min)

The encrypted-backup restore IS the migration tool — no pg_dump gymnastics:

```bash
cd ~/Downloads/backend
DATABASE_URL="postgresql+pg8000://postgres:<password>@<rds-endpoint>:5432/postgres" \
BACKUP_KEY="<your passphrase>" \
R2_ACCOUNT_ID=… R2_ACCESS_KEY_ID=… R2_SECRET_ACCESS_KEY=… R2_BUCKET=… \
  venv313/bin/python scripts/restore_backup.py <backup-date>
```

It prints per-table row counts. (Schema is created automatically on the first
boot of the web service via Alembic — run Phase 4 first if the restore
complains about missing tables, then re-run this.)

## Phase 4 — web service (App Runner, ~20 min)

1. Console → **App Runner** → Create service → Source: *Container registry* →
   pick the `equity-terminal:latest` image you pushed.
2. Port **8080**. Health check path: `/api/health`.
3. Environment variables — copy the values from Railway → web → Variables:
   `DATABASE_URL` (the RDS URL from Phase 2, `postgresql+pg8000://…`),
   `AUTH_SECRET`, `ADMIN_EMAILS`, `INDIANAPI_KEY`, `DHAN_*`, `R2_*` (until
   Phase 5 moves storage), `UNIVERSE_TIER`, `FRONTEND_ORIGIN`.
   ⚠️ Add `TRUSTED_PROXY_HOPS` = **1** to start; if sign-ins begin hitting 429
   rate limits, the proxy-hop count differs behind AWS — try `2`.
4. Create & deploy. Check: open `https://<apprunner-url>/api/health` →
   `{"status":"ok",…}`.

## Phase 5 — scheduler (ECS Fargate, ~25 min) and storage (S3, ~20 min)

**Scheduler:** Console → **ECS** → Create cluster (Fargate) → Task definition:
same ECR image, **command override** `python,scheduler.py`, 0.25 vCPU / 0.5 GB,
same environment variables as the web service (plus `BACKUP_KEY`) → Run as a
*Service* with 1 task. Check: the task's logs show the schedule banner.

**Storage:** Console → **S3** → Create bucket `equity-terminal-docs` (Mumbai).
The R2 client speaks S3's protocol — point the existing env vars at S3
(endpoint `https://s3.ap-south-1.amazonaws.com`, an IAM access key pair with
S3 access, same bucket-name variable). Copy old objects with
`aws s3 sync` from an R2-mounted rclone, or simply let new documents accumulate
in S3 — old PDFs re-fetch on demand.

## Phase 6 — frontend (Amplify) and cutover (~30 min)

1. Console → **Amplify** → Host web app → connect the
   `Manan-Jagtap/equity-terminal` GitHub repo, branch `main`. Build settings:
   the defaults detect Vite; set env var `VITE_API_URL` to the App Runner URL.
2. Deploy, open the Amplify URL, sign in, click through screener → a company →
   Valuation. Everything should look identical to the Vercel site.
3. **Cutover:** point your domain's DNS at Amplify (console shows the records);
   re-run Phase 3 once more that morning so the data is same-day fresh.
4. **Lock down:** RDS → Modify → Public access **No** (App Runner/Fargate reach
   it inside AWS); delete the Railway services and the Vercel project only
   after a full week of the AWS stack running clean.

## Phase 7 — after cutover (10 min)

- Update the GitHub uptime workflow URL (`.github/workflows/uptime.yml`) to the
  App Runner health URL.
- CI keeps working unchanged (it tests code, not infra).
- The weekly encrypted backups now flow to S3 Mumbai — data, backups, and
  compute all inside India. DPDP posture: fully satisfied.

**Rollback at any phase:** Railway/Vercel are untouched until Phase 6 step 4 —
closing the AWS tab is a full rollback.
