#!/bin/bash
# EC2 user data (first boot) for the Tank Tactics server on Amazon Linux 2023.
# The AWS deploy workflow substitutes __BUCKET__, __REGION__, __ORIGIN_HOST__, and
# __FETCH_CONFIG_B64__ and __TLS_B64__ (deploy/aws/fetch-config.sh and tls.sh, base64-encoded).
set -euxo pipefail
install -m 600 /dev/null /etc/tank-tactics.env  # may hold the Grafana Cloud token
echo '__FETCH_CONFIG_B64__' | base64 -d > /usr/local/bin/tank-tactics-config
chmod 755 /usr/local/bin/tank-tactics-config
echo '__TLS_B64__' | base64 -d > /usr/local/bin/tank-tactics-tls
chmod 755 /usr/local/bin/tank-tactics-tls
# Settings from SSM Parameter Store. Google sign-in is required: if this fails, boot stops
# here, the game never starts, and the workflow's `up` check fails.
/usr/local/bin/tank-tactics-config
dnf install -y python3.12 python3.12-pip || dnf install -y python3.11 python3.11-pip
PY=$(command -v python3.12 || command -v python3.11)
id tanktactics >/dev/null 2>&1 || useradd --system --home-dir /opt/tank-tactics --shell /sbin/nologin tanktactics
mkdir -p /opt/tank-tactics /var/lib/tank-tactics
chown tanktactics:tanktactics /var/lib/tank-tactics && chmod 700 /var/lib/tank-tactics
[ -x /opt/tank-tactics/venv/bin/python ] || "$PY" -m venv /opt/tank-tactics/venv
# The origin certificate (HTTPS on 8443, CloudFront's only way in). If it can't be had,
# boot stops here like a missing setting does, and the workflow's `up` check fails.
/usr/local/bin/tank-tactics-tls issue __ORIGIN_HOST__

cat > /usr/local/bin/tank-tactics-deploy <<'DEPLOY'
#!/bin/bash
# Fetch the latest app bundle from S3, install deps, restart. Run as root.
set -euxo pipefail
tmp=$(mktemp -d)
aws s3 cp s3://__BUCKET__/app.tar.gz "$tmp/app.tar.gz" --region __REGION__
mkdir -p "$tmp/app" && tar -xzf "$tmp/app.tar.gz" -C "$tmp/app"
/opt/tank-tactics/venv/bin/pip install --quiet -r "$tmp/app/requirements.txt"
rm -rf /opt/tank-tactics/app.old; [ -d /opt/tank-tactics/app ] && mv /opt/tank-tactics/app /opt/tank-tactics/app.old
mv "$tmp/app" /opt/tank-tactics/app && chmod -R a+rX /opt/tank-tactics/app
rm -rf "$tmp"
systemctl restart tank-tactics
DEPLOY
chmod 755 /usr/local/bin/tank-tactics-deploy

cat > /etc/systemd/system/tank-tactics.service <<'UNIT'
[Unit]
Description=Tank Tactics game server
After=network-online.target
Wants=network-online.target

[Service]
User=tanktactics
Group=tanktactics
WorkingDirectory=/opt/tank-tactics/app
Environment=HOST=0.0.0.0 PORT=8080 DB_PATH=/var/lib/tank-tactics/tank-tactics.db
EnvironmentFile=-/etc/tank-tactics.env
ExecStart=/opt/tank-tactics/venv/bin/python -m tank_tactics.server
Restart=always
RestartSec=3
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/var/lib/tank-tactics

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable tank-tactics
/usr/local/bin/tank-tactics-deploy
