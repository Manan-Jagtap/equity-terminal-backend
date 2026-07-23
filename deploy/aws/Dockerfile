# Equity Terminal backend — container image for AWS (App Runner / ECS Fargate).
# Lives under deploy/aws/ ON PURPOSE: Railway auto-builds a root-level
# Dockerfile, and the current Railway deployment must stay on Nixpacks until
# the actual cutover.
#
# One image, two roles (select by command):
#   web:        uvicorn app.main:app --host 0.0.0.0 --port 8080
#   scheduler:  python scheduler.py
#
# Build (from the repo ROOT — the build context must include app/):
#   docker build -f deploy/aws/Dockerfile -t equity-terminal-backend .
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini scheduler.py ./
COPY scripts/restore_backup.py ./scripts/restore_backup.py
COPY deploy/aws/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# SEC-11: ship the AWS ap-south-1 RDS CA bundle so DB TLS can run verify-full
# (encrypted AND the server identity checked — closes the theoretical in-VPC
# MITM). Fetched at build time over HTTPS from AWS's official truststore
# (TLS-verified by the build CA store); always current, survives AWS CA
# rotation. DORMANT until switched on with:
#   DB_SSL_VERIFY=full  RDS_CA_BUNDLE=/srv/certs/rds-ap-south-1-bundle.pem
# Rehearsed 24 Jul 2026: verify-full connected to the live RDS with this bundle.
RUN mkdir -p /srv/certs && python -c "import urllib.request; urllib.request.urlretrieve('https://truststore.pki.rds.amazonaws.com/ap-south-1/ap-south-1-bundle.pem', '/srv/certs/rds-ap-south-1-bundle.pem')" \
    && test -s /srv/certs/rds-ap-south-1-bundle.pem

# Non-root runtime user
RUN useradd -r -u 10001 terminal && chown -R terminal /srv
USER terminal

EXPOSE 8080
# FIX-25: the entrypoint runs `alembic upgrade head` fail-closed (web role) before
# handing off to the command below. Scheduler cutover passes RUN_MIGRATIONS=0.
ENTRYPOINT ["/entrypoint.sh"]
# Default role: web. Override the command for the scheduler service.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
