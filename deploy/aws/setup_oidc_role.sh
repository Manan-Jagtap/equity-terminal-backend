#!/usr/bin/env bash
# FIX-25 / OPS-03 — one-time OWNER step: create the GitHub-OIDC deploy role so
# .github/workflows/deploy.yml can build+push+cutover without long-lived keys.
#
# Run once from your laptop:   bash deploy/aws/setup_oidc_role.sh
#
# What it creates (all scoped tight):
#   1. The GitHub OIDC identity provider (token.actions.githubusercontent.com).
#   2. Role `github-deploy-equityverdict`, assumable ONLY by workflows in
#      Manan-Jagtap/equity-terminal-backend running on refs/heads/main.
#   3. An inline least-privilege policy: ECR auth+push to the equity-terminal
#      repo, ssm:SendCommand limited to the prod instance + AWS-RunShellScript,
#      and GetCommandInvocation to poll the cutover.
#
# The GitHub side is ALREADY DONE (repo secrets AWS_DEPLOY_ROLE_ARN +
# EC2_INSTANCE_ID are set; /opt/cutover.sh is on the box). After this script,
# a deploy is: Actions → Deploy → Run workflow (tick "cutover" to go live).
set -euo pipefail

ACCOUNT=593334122677
REGION=ap-south-1
ROLE=github-deploy-equityverdict
REPO="Manan-Jagtap/equity-terminal-backend"
INSTANCE=i-0f60f2dd6fc5fabd5
export AWS_PAGER=""

echo "· OIDC provider"
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1 2>/dev/null \
  || echo "  (already exists — fine)"

echo "· role trust (repo=${REPO}, branch=main only)"
TRUST=$(cat <<EOF
{"Version":"2012-10-17","Statement":[{"Effect":"Allow",
 "Principal":{"Federated":"arn:aws:iam::${ACCOUNT}:oidc-provider/token.actions.githubusercontent.com"},
 "Action":"sts:AssumeRoleWithWebIdentity",
 "Condition":{"StringEquals":{"token.actions.githubusercontent.com:aud":"sts.amazonaws.com"},
              "StringLike":{"token.actions.githubusercontent.com:sub":"repo:${REPO}:ref:refs/heads/main"}}}]}
EOF
)
aws iam create-role --role-name "$ROLE" \
  --assume-role-policy-document "$TRUST" \
  --description "GitHub Actions OIDC deploy (ECR push + SSM cutover, main only)" \
  --max-session-duration 3600 2>/dev/null \
  || aws iam update-assume-role-policy --role-name "$ROLE" --policy-document "$TRUST"

echo "· least-privilege inline policy"
POLICY=$(cat <<EOF
{"Version":"2012-10-17","Statement":[
 {"Sid":"EcrAuth","Effect":"Allow","Action":"ecr:GetAuthorizationToken","Resource":"*"},
 {"Sid":"EcrPush","Effect":"Allow",
  "Action":["ecr:BatchCheckLayerAvailability","ecr:CompleteLayerUpload","ecr:InitiateLayerUpload",
            "ecr:PutImage","ecr:UploadLayerPart","ecr:BatchGetImage","ecr:GetDownloadUrlForLayer"],
  "Resource":"arn:aws:ecr:${REGION}:${ACCOUNT}:repository/equity-terminal"},
 {"Sid":"SsmCutover","Effect":"Allow","Action":"ssm:SendCommand",
  "Resource":["arn:aws:ec2:${REGION}:${ACCOUNT}:instance/${INSTANCE}",
              "arn:aws:ssm:${REGION}::document/AWS-RunShellScript"]},
 {"Sid":"SsmPoll","Effect":"Allow","Action":"ssm:GetCommandInvocation","Resource":"*"}]}
EOF
)
aws iam put-role-policy --role-name "$ROLE" --policy-name deploy-least-priv \
  --policy-document "$POLICY"

echo "✔ done — role arn:aws:iam::${ACCOUNT}:role/${ROLE}"
echo "  First run: GitHub → Actions → Deploy → Run workflow (leave cutover"
echo "  unticked for a build-only rehearsal; tick it to go live)."
