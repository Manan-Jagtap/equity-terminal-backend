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
# Block only on TRACKED modifications — the SHA tag must match committed source.
# Untracked files (stray notes/scratch) don't affect the SHA, and the image build
# only COPYs app/, alembic, scheduler.py (guarded further by .dockerignore), so a
# root-level untracked file must not block a release. `-uno` = ignore untracked.
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "!! tracked files modified — commit them before cutting a release tag (the SHA tag must match the source)." >&2
    git status --porcelain --untracked-files=no >&2
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
else
    # DEFAULT GATE — runs when no symbol is passed.
    #
    # The symbol form above only fires if the caller remembers an argument, and
    # .github/workflows/deploy.yml never passed one: `bash build_and_push.sh`
    # with no args. So every CI deploy since 26 Jul 2026 pushed completely
    # ungated, while ROLLBACK.md claimed "build_and_push.sh's pre-push grep gate
    # would have caught it first". It could not have. Checked the logs of the
    # last real Deploy run: it goes straight from ">> building" to ">> pushed",
    # with no gate line in between.
    #
    # A symbol also has to be chosen per release, which is precisely why it got
    # skipped. This needs no argument: count a symbol that appears many times in
    # a file every build contains, and require source and image to AGREE. A
    # stale image (cached COPY layer, wrong tree, wrong arch) gives a different
    # count. Same check deploy/aws/deploy.sh step 4 uses.
    MARK_FILE=app/main.py
    MARK_STR='def '
    SRC_N=$(grep -c "$MARK_STR" "$MARK_FILE" || true)
    IMG_N=$(docker run --rm --entrypoint sh "${ECR}:${SHA}" \
              -c "grep -c '$MARK_STR' '$MARK_FILE' 2>/dev/null || true" | tr -dc '0-9')
    echo ">> pre-push gate: '${MARK_STR}' x${SRC_N} in ${MARK_FILE} (source) vs x${IMG_N:-0} (image)"
    if [ -z "$IMG_N" ] || [ "$IMG_N" = "0" ]; then
        echo "!! GATE FAILED — could not read ${MARK_FILE} inside the image. Refusing to push." >&2
        exit 1
    fi
    if [ "$IMG_N" != "$SRC_N" ]; then
        echo "!! GATE FAILED — image has ${IMG_N}, source has ${SRC_N}: the image is a DIFFERENT tree." >&2
        echo "   Refusing to push a stale build. This is the silent no-op that shipped twice." >&2
        exit 1
    fi
fi

docker push "${ECR}:${SHA}"
docker push "${ECR}:latest"

DIGEST=$(aws ecr describe-images --region "$REGION" --repository-name equity-terminal \
    --image-ids imageTag="${SHA#git-}" --query 'imageDetails[0].imageDigest' --output text 2>/dev/null || true)
echo ">> pushed ${ECR}:${SHA}  digest=${DIGEST:-<check ECR>}"
echo ">> to deploy on the box:   bash /opt/cutover.sh ${SHA}    (or 'latest')"
