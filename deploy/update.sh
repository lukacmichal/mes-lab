#!/bin/bash
# Install a new version of the code and restart the services.
# Run as root: update.sh <tarball made by git archive>
set -euo pipefail
TARBALL="${1:?usage: update.sh <tarball>}"
APP=/opt/mes-lab/app

rm -rf "$APP.new"
install -d -o mes -g mes "$APP.new"
tar -xzf "$TARBALL" -C "$APP.new"
chown -R mes:mes "$APP.new"

systemctl stop mes-lab-sim mes-lab-mes 2>/dev/null || true
rm -rf "$APP.old"
[ -d "$APP" ] && mv "$APP" "$APP.old"
mv "$APP.new" "$APP"

sudo -u mes /opt/mes-lab/venv/bin/pip install -q --disable-pip-version-check -e "$APP"
sudo -u mes bash -c 'set -a; . /etc/mes-lab/mes.env; set +a; cd /opt/mes-lab/app && /opt/mes-lab/venv/bin/python -m mes_lab.mes migrate'

systemctl start mes-lab-mes
for _ in $(seq 1 30); do
  curl -fs http://127.0.0.1:8070/healthz >/dev/null && break
  sleep 1
done
systemctl start mes-lab-sim
curl -fs http://127.0.0.1:8070/healthz && echo
