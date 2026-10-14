#!/bin/bash
# Deploy the committed HEAD to the Oracle VM (run from Git Bash on the dev machine).
#   scripts/deploy.sh
# Uncommitted changes are NOT deployed: commit first.
set -euo pipefail
HOST="${MES_HOST:-ubuntu@203.0.113.10}"
DOMAIN="${MES_DOMAIN:-203.0.113.10.sslip.io}"
KEY="${MES_SSH_KEY:-$HOME/.ssh/id_ed25519_mes}"
SSH=(ssh -i "$KEY" -o BatchMode=yes "$HOST")

cd "$(dirname "$0")/.."
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
git archive --format=tar.gz -o "$WORK/mes-lab.tar.gz" HEAD
echo "deploying $(git rev-parse --short HEAD) to $HOST"

"${SSH[@]}" "rm -rf /tmp/mes-lab-deploy && mkdir -p /tmp/mes-lab-deploy"
scp -q -i "$KEY" -o BatchMode=yes deploy/setup-server.sh deploy/update.sh deploy/*.service deploy/Caddyfile \
    "$WORK/mes-lab.tar.gz" "$HOST:/tmp/mes-lab-deploy/"
"${SSH[@]}" "sudo bash /tmp/mes-lab-deploy/setup-server.sh $DOMAIN && sudo bash /tmp/mes-lab-deploy/update.sh /tmp/mes-lab-deploy/mes-lab.tar.gz && rm -rf /tmp/mes-lab-deploy"
echo "done: https://$DOMAIN"
