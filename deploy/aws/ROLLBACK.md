# Rollback drill (FIX-25 / OPS-03)

Every image is pushed to ECR tagged **`git-<sha>`** (immutable) as well as
`:latest` (the mutable pointer the box pulls). Because the SHA tag never moves,
rolling back is deterministic: cut the box over to the previous SHA tag.

## Why this exists
`:latest` alone can't be rolled back — once you overwrite it you've lost the
prior image reference. The SHA tag is the anchor. It also defends the
*no-op-build* class of failure that shipped a stale `:latest` twice: if a build
silently produced the wrong image, its SHA tag simply wouldn't exist / wouldn't
match, and `build_and_push.sh`'s pre-push grep gate would have caught it first.

## Find the previous good build
```bash
aws ecr describe-images --region ap-south-1 --repository-name equity-terminal \
  --query 'reverse(sort_by(imageDetails,&imagePushedAt))[:6].{pushedAt:imagePushedAt,tags:imageTags}' \
  --output table
```
Pick the `git-<sha>` you want to return to (the one running *before* the bad deploy).

## Roll back (on the box, via SSM)
```bash
aws ssm send-command --region ap-south-1 --instance-ids i-0f60f2dd6fc5fabd5 \
  --document-name AWS-RunShellScript \
  --parameters 'commands=["bash /opt/cutover.sh git-<previous-sha>"]' \
  --query Command.CommandId --output text
```
`cutover.sh` pulls that exact image, recreates `web` + `scheduler`, and **waits
for `web` to report healthy** before returning success. A fail-closed migration
(the new image crashing at boot) is exactly the case this recovers from — the
previous image's schema is already at head, so it comes straight back up.

## Confirm service restored
```bash
bash deploy/aws/smoke.sh https://api.equityverdict.com
```

## Re-point `:latest` (optional, after a rollback)
Rolling back by SHA leaves `:latest` pointing at the bad build. To realign so a
plain `cutover.sh latest` is correct again, re-tag in ECR:
```bash
MANIFEST=$(aws ecr batch-get-image --region ap-south-1 --repository-name equity-terminal \
  --image-ids imageTag=git-<previous-sha> --query 'images[0].imageManifest' --output text)
aws ecr put-image --region ap-south-1 --repository-name equity-terminal \
  --image-tag latest --image-manifest "$MANIFEST"
```

## Forward-fix instead
If the failure is a bad migration (not app code), prefer a forward fix: write the
corrective Alembic revision, `build_and_push.sh`, `cutover.sh latest`. The
boot-time `alembic upgrade head` applies it fail-closed.

---

### Enabling the CI-gated deploy (owner, one-time)
`.github/workflows/deploy.yml` builds + pushes the SHA-tagged image and (opt-in)
cuts over via SSM + smokes. It needs, as **owner infra**:
1. An IAM role for GitHub OIDC (`token.actions.githubusercontent.com`) with ECR
   push + `ssm:SendCommand` on the instance. Add its ARN as repo secret
   `AWS_DEPLOY_ROLE_ARN`, and the instance id as `EC2_INSTANCE_ID`.
2. Upload `deploy/aws/cutover.sh` and `deploy/aws/Caddyfile` to
   `s3://equity-terminal-config-593334122677/` so `user-data.sh` can fetch them.

Until that's set up, deploys stay manual with the same committed scripts:
`bash deploy/aws/build_and_push.sh <gate-symbol>` on the build host, then
`bash /opt/cutover.sh latest` on the box.
