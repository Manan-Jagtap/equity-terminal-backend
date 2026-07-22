#!/bin/bash
# FIX-25 (OPS-02 / ARCH-01) — committed EC2 bootstrap so the box is rebuildable
# from source. Reflects the CURRENT topology: caddy (TLS on 80/443) + web +
# scheduler, all on the `edge` docker network so caddy resolves web:8080 by name.
# Secrets live only in S3 (ec2.env), fetched by the instance role. Paste this as
# the instance's user-data when launching a replacement.
set -euxo pipefail
exec > /var/log/user-data.log 2>&1

dnf install -y docker
systemctl enable --now docker

# 2G swap — t3.micro has 1GB RAM; valuation batches need headroom.
if [ ! -f /swapfile ]; then
    fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

REGION=ap-south-1
BUCKET=s3://equity-terminal-config-593334122677
ECR=593334122677.dkr.ecr.ap-south-1.amazonaws.com/equity-terminal

# Config from S3 (instance role has GetObject on exactly these keys).
aws s3 cp "${BUCKET}/ec2.env" /opt/app.env --region "$REGION"
chmod 600 /opt/app.env

# Reverse-proxy config — committed at deploy/aws/Caddyfile; kept in the same
# config bucket so a rebuild is self-contained.
aws s3 cp "${BUCKET}/Caddyfile" /opt/Caddyfile --region "$REGION"

# Committed cutover script (recreate/rollback in one command) onto the box.
aws s3 cp "${BUCKET}/cutover.sh" /opt/cutover.sh --region "$REGION" && chmod +x /opt/cutover.sh || true

# Private docker network so caddy can reach the app containers by name.
docker network create edge || true

# TLS reverse proxy.
docker run -d --name caddy --restart always --network edge -p 80:80 -p 443:443 \
    -v /opt/Caddyfile:/etc/caddy/Caddyfile -v caddy_data:/data -v caddy_config:/config caddy:2

# App: web (migrates at boot, fail-closed) + scheduler (RUN_MIGRATIONS=0).
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${ECR%/*}"
docker pull "${ECR}:latest"
docker run -d --name web --network edge --restart always --env-file /opt/app.env \
    --log-opt max-size=10m --log-opt max-file=3 "${ECR}:latest"
docker run -d --name scheduler --network edge --restart always --env-file /opt/app.env \
    -e RUN_MIGRATIONS=0 --log-opt max-size=10m --log-opt max-file=3 "${ECR}:latest" python scheduler.py
