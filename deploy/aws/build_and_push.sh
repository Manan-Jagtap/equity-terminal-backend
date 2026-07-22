#!/usr/bin/env bash
# FIX-25 (OPS-03 / ARCH-01) — build the backend image and push it to ECR tagged
# by the exact git SHA (immutable) AND :latest (the mutable pointer the box
# pulls). The SHA tag is what makes a rollback deterministic — see ROLLBACK.md.
#
# Runs anywhere with docker + AWS creds (CI ubuntu-amd64 runner = no QEMU cross-
# compile; the owner's Apple-silicon Mac needs --platform, kept below for both).
#
# Usage:  bash deploy/aws/build_and_push.sh [GATE_SYMBOL]
#   GATE_SYMBOL (optional): a source symbol grepped INSIDE the built image before
#   push — the guard against the silent no-op build that shipped stale :latest
#   twice (see MIGRATION_AWS.md "Deploy gate").
set -euo pipefail

ECR=593334122677.dkr.ecr.ap-south-1.amazonaws.com/equity-terminal
REGION=ap-south-1
GATE_SYMBOL="${1:-}"

cd "$(git rev-parse --show-toplevel)"
if [ -n "$(git status --porcelain)" ]; then
    echo "!! working tree is dirty — commit before cutting a release tag (the SHA tag must match the source)." >&2
    exit 1
fi
SHA="git-$(git rev-parse --short HEAD)"

aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${ECR%/*}"

echo ">> building ${ECR}:${SHA} (+ :latest)"
docker build --no-cache --platform linux/amd64 -f deploy/aws/Dockerfile \
    -t "${ECR}:${SHA}" -t "${ECR}:latest" .

if [ -n "$GATE_SYMBOL" ]; then
    echo ">> pre-push gate: '${GATE_SYMBOL}' must be present in the built image"
    docker run --rm --entrypoint sh "${ECR}:${SHA}" -c "grep -rq -- '${GATE_SYMBOL}' app/" \
        || { echo "!! GATE FAILED — '${GATE_SYMBOL}' not in the image; refusing to push a stale build." >&2; exit 1; }
fi

docker push "${ECR}:${SHA}"
docker push "${ECR}:latest"

DIGEST=$(aws ecr describe-images --region "$REGION" --repository-name equity-terminal \
    --image-ids imageTag="${SHA#git-}" --query 'imageDetails[0].imageDigest' --output text 2>/dev/null || true)
echo ">> pushed ${ECR}:${SHA}  digest=${DIGEST:-<check ECR>}"
echo ">> to deploy on the box:   bash /opt/cutover.sh ${SHA}    (or 'latest')"
