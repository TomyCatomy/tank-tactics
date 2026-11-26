#!/bin/bash
# Sets up one environment of the Tank Tactics server and ships the current code bundle to it.
# Usage (as root on the server): tank-tactics-install production|staging
#
# Both environments run on the same server, each as its own systemd unit, system user,
# venv and database, so staging can never read or overwrite production's games:
#
#   production  unit tank-tactics          port 8080  /var/lib/tank-tactics
#   staging     unit tank-tactics-staging  port 8081  /var/lib/tank-tactics-staging
#
# Staging has the debug.* game options on and is capped in CPU and memory so it can't
# starve production or drain the instance's CPU credits.
#
# The AWS deploy workflow substitutes __BUCKET__ and __REGION__, installs this as
# /usr/local/bin/tank-tactics-install, and runs it on first boot (for both environments)
# and on every redeploy (for the one being deployed). Everything before the code swap is
# idempotent, so a redeploy also brings an older server's layout up to date.
set -euxo pipefail

case "${1:-}" in
  production)
    name=tank-tactics port=8080 user=tanktactics
    extra=""
    ;;
  staging)
    name=tank-tactics-staging port=8081 user=tanktactics-staging
    extra=$'Environment=ALLOW_DEBUG=true\nCPUQuota=50%\nCPUWeight=20\nMemoryMax=512M'
    ;;
  *)
    echo "usage: $0 production|staging" >&2
    exit 2
    ;;
esac

home=/opt/$name data=/var/lib/$name
PY=$(command -v python3.12 || command -v python3.11)
id "$user" >/dev/null 2>&1 || useradd --system --home-dir "$home" --shell /sbin/nologin "$user"
mkdir -p "$home" "$data"
chown "$user:$user" "$data" && chmod 700 "$data"
[ -x "$home/venv/bin/python" ] || "$PY" -m venv "$home/venv"

cat > "/etc/systemd/system/$name.service" <<UNIT
[Unit]
Description=Tank Tactics game server ($1)
After=network-online.target
Wants=network-online.target

[Service]
User=$user
Group=$user
WorkingDirectory=$home/app
Environment=HOST=0.0.0.0 PORT=$port DB_PATH=$data/tank-tactics.db
EnvironmentFile=-/etc/tank-tactics.env
ExecStart=$home/venv/bin/python -m tank_tactics.server
Restart=always
RestartSec=3
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=$data
$extra

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable "$name"
# Servers created before staging existed shipped code with this script instead.
rm -f /usr/local/bin/tank-tactics-deploy

tmp=$(mktemp -d)
aws s3 cp s3://__BUCKET__/app.tar.gz "$tmp/app.tar.gz" --region __REGION__
mkdir -p "$tmp/app" && tar -xzf "$tmp/app.tar.gz" -C "$tmp/app"
"$home/venv/bin/pip" install --quiet -r "$tmp/app/requirements.txt"
rm -rf "$home/app.old"; [ -d "$home/app" ] && mv "$home/app" "$home/app.old"
mv "$tmp/app" "$home/app" && chmod -R a+rX "$home/app"
rm -rf "$tmp"
systemctl restart "$name"
