#!/bin/bash
# One-time (and idempotent) preparation of the Oracle VM for MES lab.
# Run as root: setup-server.sh <domain>
# Installs PostgreSQL, Python and Caddy, opens ports 80/443 in the host
# firewall, creates the service user, the database and /etc/mes-lab/mes.env
# with freshly generated secrets, and installs the systemd units.
set -euo pipefail
DOMAIN="${1:?usage: setup-server.sh <domain>}"
HERE="$(cd "$(dirname "$0")" && pwd)"

export DEBIAN_FRONTEND=noninteractive
if ! command -v caddy >/dev/null || ! command -v psql >/dev/null; then
  apt-get update -q
  apt-get install -y -q postgresql python3-venv python3-pip caddy netfilter-persistent iptables-persistent
fi

# service user and folders
id mes >/dev/null 2>&1 || useradd --system --home-dir /opt/mes-lab --shell /usr/sbin/nologin mes
install -d -o mes -g mes -m 750 /opt/mes-lab /opt/mes-lab/var
install -d -o root -g mes -m 750 /etc/mes-lab
[ -x /opt/mes-lab/venv/bin/python ] || sudo -u mes python3 -m venv /opt/mes-lab/venv

# database and secrets (generated once, never leave the server)
if [ ! -f /etc/mes-lab/mes.env ]; then
  DBPASS="$(openssl rand -hex 20)"
  sudo -u postgres psql -v ON_ERROR_STOP=1 -q -c "CREATE ROLE mes LOGIN PASSWORD '${DBPASS}'"
  sudo -u postgres createdb -O mes mes
  cat > /etc/mes-lab/mes.env <<EOF
MES_DATABASE_URL=postgresql+psycopg://mes:${DBPASS}@127.0.0.1:5432/mes
MES_EDGE_TOKEN=$(openssl rand -hex 24)
MES_SECRET_KEY=$(openssl rand -hex 32)
MES_LOGIN=actions
MES_COOKIE_SECURE=1
MES_RETENTION_DAYS=21
MES_URL=http://127.0.0.1:8070
SIM_SPEED=60
EOF
fi
# older servers: MES_REQUIRE_LOGIN=1 -> anybody may look, changes need a sign-in
if ! grep -q '^MES_LOGIN=' /etc/mes-lab/mes.env; then
  sed -i '/^MES_REQUIRE_LOGIN=/d' /etc/mes-lab/mes.env
  echo 'MES_LOGIN=actions' >> /etc/mes-lab/mes.env
fi
chown root:mes /etc/mes-lab/mes.env
chmod 640 /etc/mes-lab/mes.env

# PostgreSQL: memory for a 12 GB machine; commits need not wait for the disk
PGCONF="$(ls -d /etc/postgresql/*/main | sort -V | tail -1)/conf.d/mes-lab.conf"
if [ ! -f "$PGCONF" ]; then
  cat > "$PGCONF" <<EOF
shared_buffers = 2GB
effective_cache_size = 6GB
work_mem = 16MB
maintenance_work_mem = 256MB
synchronous_commit = off
EOF
  systemctl restart postgresql
fi
chmod 644 "$PGCONF"
pg_isready -q -h 127.0.0.1 || { systemctl restart postgresql; sleep 3; pg_isready -h 127.0.0.1; }

# host firewall: Oracle's Ubuntu image only lets SSH in; put 80/443 before its REJECT
for port in 80 443; do
  if ! iptables -C INPUT -p tcp --dport "$port" -m state --state NEW -j ACCEPT 2>/dev/null; then
    pos="$(iptables -L INPUT --line-numbers -n | awk '/REJECT/ {print $1; exit}')"
    iptables -I INPUT "${pos:-1}" -p tcp --dport "$port" -m state --state NEW -j ACCEPT
  fi
done
netfilter-persistent save >/dev/null

# services and reverse proxy
install -m 644 "$HERE/mes-lab-mes.service" "$HERE/mes-lab-sim.service" /etc/systemd/system/
sed "s/__DOMAIN__/${DOMAIN}/" "$HERE/Caddyfile" > /etc/caddy/Caddyfile
systemctl daemon-reload
systemctl enable -q mes-lab-mes mes-lab-sim caddy
systemctl reload-or-restart caddy
echo "server ready for ${DOMAIN}"
