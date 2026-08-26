#!/usr/bin/env bash
#
# deploy.sh — build, verify, push and cut over the backend.
#
#   ./deploy/aws/deploy.sh              full deploy
#   ./deploy/aws/deploy.sh --no-cutover build + push only, cut over yourself
#   ./deploy/aws/deploy.sh --check      verify what is live, change nothing
#
# WHY THIS EXISTS
#
# Four backend deploys were attempted by hand in Aug 2026. Three of them merged
# the PR, ran the cutover, and shipped NOTHING — because the build step failed
# or never ran, so the cutover pulled an unchanged `:latest` and restarted the
# same old container. Every time, /api/health came back green, because the OLD
# code is perfectly healthy. It is just old.
#
# So this script's real job is not convenience. It is refusing to report success
# it has not verified. Every gate below exists because that exact thing happened:
#
#   1. Docker daemon not running   -> `docker build` died on the socket, and
#                                     because the runbook chains with &&,
#                                     everything after silently skipped.
#   2. Missing -f deploy/aws/...   -> the Dockerfile is not at repo root, so
#                                     `docker build .` finds nothing, fails, and
#                                     a chained `docker push` re-pushed the
#                                     STALE cached :latest.
#   3. Stale local checkout        -> built without `git pull`, so a FRESH image
#                                     was pushed containing OLD code.
#   4. zsh ate the tag             -> `$ECR:latest` parses as ${ECR:l} + "atest".
#                                     Always ${ECR}:latest with braces.
#   5. Cutover run locally         -> "No such container: web". It must go to the
#                                     instance over SSM.
#
# And the gate that was missing entirely: LOOK INSIDE THE IMAGE BEFORE PUSHING.
#
set -Eeuo pipefail

# REPO_DIR resolves from THIS script's own location (…/deploy/aws/deploy.sh
# → repo root), so moving the checkout can never point a deploy at a stale
# copy — failure mode #3 above, arrived at from a different direction. It was
# hardcoded to ~/Downloads/backend until 26 Aug 2026, when the repo moved out
# of ~/Downloads (macOS restricts that folder) and this default went dead.
_SELF_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REPO_DIR="${REPO_DIR:-$_SELF_REPO}"
REGION="ap-south-1"
ACCOUNT="593334122677"
ECR="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/equity-terminal"
INSTANCE="i-0f60f2dd6fc5fabd5"
API="https://api.equityverdict.com"

MODE="deploy"
[[ "${1:-}" == "--no-cutover" ]] && MODE="build"
[[ "${1:-}" == "--check" ]]      && MODE="check"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
die()  { printf '\n  \033[31m✗ STOP: %s\033[0m\n\n' "$*" >&2; exit 1; }

trap 'die "failed at line $LINENO — nothing further ran"' ERR

# ── live state ───────────────────────────────────────────────────────────────
ecr_digest() {
  aws ecr describe-images --repository-name equity-terminal --region "$REGION" \
    --image-ids imageTag=latest --query 'imageDetails[0].imageDigest' --output text 2>/dev/null || echo "none"
}
# NOTE: .RepoDigests is an IMAGE field. `docker inspect web` is a CONTAINER and
# has no such key ("map has no entry for key RepoDigests"), so resolve the
# container's image first. This is the ECR MANIFEST digest, which is what
# `aws ecr describe-images` reports — the two are directly comparable.
running_digest() {
  local cid
  cid=$(aws ssm send-command --region "$REGION" --instance-ids "$INSTANCE" \
      --document-name AWS-RunShellScript \
      --parameters commands='["IMG=$(docker inspect web --format \"{{.Image}}\"); docker image inspect $IMG --format \"{{index .RepoDigests 0}}\" 2>/dev/null || echo none"]' \
      --query 'Command.CommandId' --output text)
  sleep 8
  aws ssm get-command-invocation --region "$REGION" --command-id "$cid" \
    --instance-id "$INSTANCE" --query 'StandardOutputContent' --output text 2>/dev/null \
    | tr -d '\r\n ' | sed 's/.*@//'   # -> sha256:<hex>
}

if [[ "$MODE" == "check" ]]; then
  bold "Live state"
  echo "  ECR :latest   $(ecr_digest)"
  echo "  running web   $(running_digest)"
  echo "  health        $(curl -fsS "$API/api/health" || echo unreachable)"
  echo
  echo "  Matching digests mean the running container IS the pushed image."
  echo "  Green health does NOT mean that — the old code is healthy too."
  exit 0
fi

# ── 1. preconditions ─────────────────────────────────────────────────────────
bold "1/6  Preconditions"
cd "$REPO_DIR" || die "no repo at $REPO_DIR"
docker info >/dev/null 2>&1 || die "Docker Desktop is not running. Start it, wait for the whale to settle, re-run."
ok "docker daemon up ($(docker info --format '{{.ServerVersion}}'))"
[[ -f deploy/aws/Dockerfile ]] || die "deploy/aws/Dockerfile missing — are you in the backend repo?"
ok "Dockerfile present"
aws sts get-caller-identity >/dev/null 2>&1 || die "AWS CLI not authenticated"
ok "aws cli authenticated"

# ── 2. source ────────────────────────────────────────────────────────────────
bold "2/6  Source"
git checkout main >/dev/null 2>&1
git pull --ff-only
HEAD_SHA=$(git rev-parse --short HEAD)
ok "main @ $HEAD_SHA — $(git log -1 --format=%s | cut -c1-60)"
[[ -z "$(git status --porcelain --untracked-files=no)" ]] \
  || echo "  ! uncommitted tracked changes — they WILL be baked into the image"

# The marker: a symbol that exists in this commit. Any file+string works; the
# point is to prove the built image contains THIS tree, not a cached older one.
MARK_FILE="${MARK_FILE:-app/main.py}"
MARK_STR="${MARK_STR:-def }"
SRC_N=$(grep -c "$MARK_STR" "$MARK_FILE" || true)
[[ "$SRC_N" -gt 0 ]] || die "marker '$MARK_STR' not found in $MARK_FILE — pick another with MARK_FILE/MARK_STR"
ok "source marker: $SRC_N × '$MARK_STR' in $MARK_FILE"

BEFORE=$(ecr_digest)
echo "  ECR :latest before  $BEFORE"

# ── 3. build ─────────────────────────────────────────────────────────────────
bold "3/6  Build (linux/amd64 under emulation — 5-15 min is normal, not a hang)"
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com" >/dev/null \
  || die "ECR login failed"
docker build --no-cache --platform linux/amd64 \
  -f deploy/aws/Dockerfile \
  -t "${ECR}:latest" -t "${ECR}:git-${HEAD_SHA}" . \
  || die "build failed — read the error above; NOTHING was pushed"
ok "image built"

# ── 4. THE GATE ──────────────────────────────────────────────────────────────
# This is the step whose absence caused three silent no-op deploys. A build can
# succeed and still contain the wrong tree (stale checkout, cached COPY layer).
bold "4/6  Verify the image BEFORE pushing"
IMG_N=$(docker run --rm --platform linux/amd64 "${ECR}:latest" \
          sh -c "grep -c '$MARK_STR' '$MARK_FILE' 2>/dev/null || echo 0" | tr -dc '0-9')
[[ -n "$IMG_N" && "$IMG_N" -gt 0 ]] || die "image does not contain '$MARK_STR' — it is NOT this tree. Not pushing."
[[ "$IMG_N" == "$SRC_N" ]] \
  || die "image has $IMG_N × '$MARK_STR', source has $SRC_N — the image is a DIFFERENT tree. Not pushing."
ok "image matches source ($IMG_N × '$MARK_STR')"

# ── 5. push ──────────────────────────────────────────────────────────────────
bold "5/6  Push"
docker push "${ECR}:latest"      || die "push failed"
docker push "${ECR}:git-${HEAD_SHA}" >/dev/null || true
AFTER=$(ecr_digest)
[[ "$AFTER" != "$BEFORE" ]] \
  || die "ECR digest did not change ($AFTER). The push was a no-op — do NOT cut over."
ok "ECR digest $BEFORE → $AFTER"

if [[ "$MODE" == "build" ]]; then
  echo; bold "Built and pushed. Cutover skipped (--no-cutover)."; exit 0
fi

# ── 6. cutover + verify ──────────────────────────────────────────────────────
bold "6/6  Cutover"
CID=$(aws ssm send-command --region "$REGION" --instance-ids "$INSTANCE" \
        --document-name AWS-RunShellScript \
        --parameters 'commands=["bash /opt/cutover.sh latest 2>&1 | tail -20"]' \
        --query 'Command.CommandId' --output text) || die "SSM send-command failed"
echo "  ssm command: $CID"
for _ in $(seq 1 24); do
  ST=$(aws ssm get-command-invocation --region "$REGION" --command-id "$CID" \
        --instance-id "$INSTANCE" --query 'Status' --output text 2>/dev/null || echo Pending)
  [[ "$ST" == "Success" || "$ST" == "Failed" ]] && break
  sleep 5
done
[[ "$ST" == "Success" ]] || die "cutover status: $ST — inspect with: aws ssm get-command-invocation --region $REGION --command-id $CID --instance-id $INSTANCE"
ok "cutover ran"

# uvicorn needs a moment; checking too early returns a 502 that is startup, not failure
echo "  waiting 30s for the container to come up…"
sleep 30

RUN=$(running_digest)
if [[ "$RUN" == "$AFTER" ]]; then
  ok "running container IS the pushed image (${RUN:0:23}…)"
else
  die "running container is ${RUN:0:23}… but ECR is ${AFTER:0:23}… — the cutover did not take the new image"
fi

HTTP=$(curl -s -o /dev/null -w '%{http_code}' "$API/api/health" || echo 000)
echo "  health: $HTTP"
echo
bold "Deployed and verified."
echo "  Digest match is the proof, not the health check."
