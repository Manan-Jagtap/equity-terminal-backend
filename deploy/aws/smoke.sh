#!/usr/bin/env bash
# FIX-25 (OPS-03) — post-deploy smoke test. Hits the public health endpoint and
# fails non-zero if the app isn't serving cleanly, so a broken deploy is caught
# by the deploy pipeline instead of by users.
#
# Usage:  bash deploy/aws/smoke.sh [BASE_URL]   (default https://api.equityverdict.com)
set -euo pipefail
BASE="${1:-https://api.equityverdict.com}"

echo ">> GET ${BASE}/api/health"
body="$(curl -fsS --max-time 20 "${BASE}/api/health")" || { echo "SMOKE FAIL: health endpoint unreachable" >&2; exit 1; }
echo "$body"

echo "$body" | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"' \
    || { echo "SMOKE FAIL: status != ok" >&2; exit 1; }
# errors_1h > 0 is a warning, not a hard fail (a single transient error shouldn't
# block a deploy) — but surface it loudly.
echo "$body" | grep -q '"errors_1h"[[:space:]]*:[[:space:]]*0' \
    || echo ">> WARN: errors_1h > 0 — check /api/health and error_log"
echo "SMOKE OK"
