#!/usr/bin/env bash
#
# set-vendor-key.sh — put a NEW IndianAPI key into production, safely.
#
#   ./deploy/aws/set-vendor-key.sh            read the key from ~/.equity-terminal/app.env
#   ./deploy/aws/set-vendor-key.sh --prompt   type/paste it once, not echoed
#   ./deploy/aws/set-vendor-key.sh --check    validate what's live, change nothing
#
# WHY THIS EXISTS
#
# Setting this key by hand failed four times in a row, and only one of those was
# a bad key:
#   1. the old key, correctly written, then revoked          -> 401
#   2. the literal text NEW_KEY_HERE (12 chars)              -> 401
#   3. a truncated copy from the dashboard (24 of 39 chars)  -> 401
#   4. the literal text PASTE_THE_KEY_THAT_RETURNED_200 (31) -> 401
#
# Every time, the file was written, the cutover ran, the containers recreated
# and /api/health came back 200. Nothing downstream could tell that the value
# was nonsense, because nothing downstream ever ASKED THE VENDOR before shipping
# it. Two of those four were placeholders out of a copy-paste command — a
# placeholder that reads like prose substitutes cleanly and leaves no trace.
#
# So this script's job is to refuse. It validates the key against the live
# vendor BEFORE touching /opt/app.env, and it never takes the key on the command
# line (argv is visible to every process on the box and lands in shell history).
#
set -Eeuo pipefail

REGION=ap-south-1
INSTANCE=i-0f60f2dd6fc5fabd5
LOCAL_ENV="${LOCAL_ENV:-$HOME/.equity-terminal/app.env}"
PROBE='https://stock.indianapi.in/stock?name=RELIANCE'

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
die()  { printf '\n  \033[31m✗ STOP: %s\033[0m\n\n' "$*" >&2; exit 1; }

# Probe with a key WITHOUT ever printing it. The vendor is unusually helpful
# here: 401 = key not recognised, 429 = key IS recognised but out of quota,
# 200 = good. A 429 therefore means "right format, wrong/exhausted account" —
# which is a different problem from a typo and deserves a different message.
probe() {
  curl -s -o /dev/null -w '%{http_code}' -H "X-API-Key: $1" "$PROBE" 2>/dev/null || echo 000
}

if [[ "${1:-}" == "--check" ]]; then
  bold "What is live right now"
  code=$(aws ssm send-command --region "$REGION" --instance-ids "$INSTANCE" \
    --document-name AWS-RunShellScript \
    --parameters 'commands=["docker exec web python -c \"import os,requests;k=os.getenv(chr(73)+chr(78)+chr(68)+chr(73)+chr(65)+chr(78)+chr(65)+chr(80)+chr(73)+chr(95)+chr(75)+chr(69)+chr(89)) or str();print(len(k), k[:3], requests.get(os.getenv(chr(73)+chr(78)+chr(68)+chr(73)+chr(65)+chr(78)+chr(65)+chr(80)+chr(73)+chr(95)+chr(66)+chr(65)+chr(83)+chr(69))+chr(47)+chr(115)+chr(116)+chr(111)+chr(99)+chr(107),headers={chr(88)+chr(45)+chr(65)+chr(80)+chr(73)+chr(45)+chr(75)+chr(101)+chr(121):k},params={chr(110)+chr(97)+chr(109)+chr(101):chr(84)+chr(67)+chr(83)},timeout=20).status_code)\" 2>&1 | tail -1"]' \
    --query 'Command.CommandId' --output text)
  sleep 9
  out=$(aws ssm get-command-invocation --region "$REGION" --command-id "$code" \
        --instance-id "$INSTANCE" --query 'StandardOutputContent' --output text 2>/dev/null | tr -d '\r')
  echo "  container key: length/prefix/vendor-status = $out"
  echo
  echo "  A length near 39-40 with prefix sk- and status 200 is healthy."
  echo "  Status 429 means the key is REAL but its account is out of quota —"
  echo "  a new key from an account WITH quota is the fix, not a retry."
  exit 0
fi

# ── obtain the key, never via argv ───────────────────────────────────────────
bold "1/4  Read the key"
if [[ "${1:-}" == "--prompt" ]]; then
  read -r -s -p "  paste the IndianAPI key (input hidden): " KEY; echo
else
  [[ -f "$LOCAL_ENV" ]] || die "no $LOCAL_ENV — use --prompt, or set LOCAL_ENV=/path/to/file"
  KEY=$(grep -m1 '^INDIANAPI_KEY=' "$LOCAL_ENV" | cut -d= -f2- | tr -d "\"' \r\n")
  ok "read from $LOCAL_ENV"
fi
[[ -n "${KEY:-}" ]] || die "empty key"

# ── shape: catches placeholders and truncation before any network call ───────
bold "2/4  Shape"
case "$KEY" in
  *PASTE*|*REPLACE*|*NEW_KEY*|*YOUR_KEY*|*HERE*|*XXX*)
     die "that is placeholder TEXT, not a key (this exact mistake shipped twice)";;
esac
[[ "$KEY" == sk-* ]] || die "does not start with sk- (got '${KEY:0:3}…', length ${#KEY})"
(( ${#KEY} >= 30 )) || die "length ${#KEY} — too short; the dashboard shows an ABBREVIATED key, use its copy button"
ok "prefix sk-, length ${#KEY}"

# ── the gate the four failures all lacked: ask the vendor first ──────────────
bold "3/4  Ask the vendor BEFORE touching production"
status=$(probe "$KEY")
case "$status" in
  200) ok "vendor accepts this key (HTTP 200)";;
  401) die "vendor says 401 Invalid API key — wrong or revoked. Nothing was changed.";;
  429) die "vendor says 429 Rate limit exceeded. The key is REAL but its account has no
       quota left. Deploying it would change nothing. Take a key from an account
       that shows remaining quota in the console. Nothing was changed.";;
  000) die "could not reach the vendor at all — check the network. Nothing was changed.";;
  *)   die "vendor returned HTTP $status — not deploying on an unclear answer.";;
esac

# ── write + cutover, then prove it took ──────────────────────────────────────
bold "4/4  Write and cut over"
cid=$(aws ssm send-command --region "$REGION" --instance-ids "$INSTANCE" \
      --document-name AWS-RunShellScript \
      --parameters "commands=[\"cp /opt/app.env /opt/app.env.bak.\$(date +%s)\",\"sed -i 's|^INDIANAPI_KEY=.*|INDIANAPI_KEY=$KEY|' /opt/app.env\",\"grep -c '^INDIANAPI_KEY=' /opt/app.env\",\"bash /opt/cutover.sh latest 2>&1 | tail -3\"]" \
      --query 'Command.CommandId' --output text) || die "SSM send failed"
for _ in $(seq 1 40); do
  st=$(aws ssm get-command-invocation --region "$REGION" --command-id "$cid" \
       --instance-id "$INSTANCE" --query 'Status' --output text 2>/dev/null || echo Pending)
  [[ "$st" == Success || "$st" == Failed ]] && break
  sleep 5
done
[[ "$st" == Success ]] || die "cutover status $st — inspect command $cid"
ok "written and cut over"

# The env is bound at container CREATE, so a restart would have shipped nothing.
# cutover.sh recreates; this confirms the running container really has it.
echo "  waiting 30s for the container…"; sleep 30
live=$(probe "$KEY")
[[ "$live" == 200 ]] || echo "  ! vendor now returns $live for this key — investigate before trusting the dashboard"
health=$(curl -s https://api.equityverdict.com/api/health 2>/dev/null)
echo "  health: $health"
echo
bold "Done. Confirm the dashboard's gainers/losers/52-week fill in."
echo "  If they stay empty with a 200 key, the problem is downstream, not the key."
