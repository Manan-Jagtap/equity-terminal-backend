#!/usr/bin/env bash
# FIX-25 (OPS-02) — recreate the running containers from an ECR tag. Committed so
# a deploy (and a rollback) is one reviewed command, not ad-hoc shell. Runs ON
# the box (place at /opt/cutover.sh; user-data does this). NO docker-compose.
#
# Usage:  bash /opt/cutover.sh [TAG]
#   TAG defaults to 'latest'; pass a 'git-<sha>' tag to deploy or ROLL BACK to a
#   specific immutable build (see ROLLBACK.md).
#
# web migrates at boot (fail-closed, via the image ENTRYPOINT); scheduler skips
# migrations (RUN_MIGRATIONS=0). FRONTEND_ORIGIN and all secrets come from the
# single source of truth /opt/app.env. caddy (already running on `edge`) proxies
# web:8080 and reconnects after the few-second recreate blip.
set -euo pipefail

ECR=593334122677.dkr.ecr.ap-south-1.amazonaws.com/equity-terminal
REGION=ap-south-1
TAG="${1:-latest}"
IMG="${ECR}:${TAG}"

aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${ECR%/*}"
docker pull "$IMG"

echo ">> cutting over web + scheduler to ${IMG}"
docker rm -f web 2>/dev/null || true
docker run -d --name web --network edge --restart always --env-file /opt/app.env \
    --log-opt max-size=10m --log-opt max-file=3 "$IMG"

docker rm -f scheduler 2>/dev/null || true
docker run -d --name scheduler --network edge --restart always --env-file /opt/app.env \
    -e RUN_MIGRATIONS=0 --log-opt max-size=10m --log-opt max-file=3 "$IMG" python scheduler.py

# SCALE-05: reclaim disk. Every deploy pulls a new ~500MB image; superseded
# layers accumulate and a full 16GB root takes down all three containers at once
# (and blocks the SSM fix-deploy). Prune anything older than 72h — recent
# git-<sha> rollback targets survive.
docker system prune -af --filter until=72h >/dev/null 2>&1 || true

# Fail-closed verification: a bad migration crashes web at boot, so wait for it to
# actually report healthy before calling the cutover done.
echo ">> waiting for web to report healthy…"
for i in $(seq 1 30); do
    sleep 2
    if docker exec web python -c "import urllib.request,json,sys; sys.exit(0 if json.load(urllib.request.urlopen('http://localhost:8080/api/health')).get('status')=='ok' else 1)" 2>/dev/null; then
        echo ">> web healthy on ${IMG}"
        exit 0
    fi
done
echo "!! web did not report healthy within 60s — inspect 'docker logs web' (a failed migration is fail-closed by design)." >&2
echo "!! roll back with:  bash /opt/cutover.sh git-<previous-sha>   (see ROLLBACK.md)" >&2
exit 1
