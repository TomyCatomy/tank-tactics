#!/bin/bash
# The origin's TLS certificate, so CloudFront reaches the server over HTTPS and seat
# tokens, sign-ins and chat are never sent in the clear between CloudFront and EC2.
# The AWS deploy workflow installs this as /usr/local/bin/tank-tactics-tls. Run as root.
#
#   tank-tactics-tls issue <host>   first boot and every redeploy: installs certbot if needed,
#                                    gets a Let's Encrypt certificate for <host> unless a
#                                    valid one exists, and points the app at it (HTTPS on 8443)
#   tank-tactics-tls install        certbot's hook after a renewal: copies the new files to
#                                    the app and restarts it if it is running
#
# Let's Encrypt checks control of <host> with a DNS TXT record (_acme-challenge.<host>) that
# certbot writes in Route 53 using the instance role, so no port has to be open to the
# internet. A systemd timer renews the certificate (Let's Encrypt certificates last 90 days).
set -euo pipefail

CERTBOT_DIR=/opt/certbot
CERTBOT=$CERTBOT_DIR/bin/certbot
TLS_DIR=/var/lib/tank-tactics/tls
HOST_FILE=/etc/tank-tactics-origin
DROPIN=/etc/systemd/system/tank-tactics.service.d/tls.conf
PORT=8443

install_certbot() {
  [ -x "$CERTBOT" ] && return
  local py
  py=$(command -v python3.12 || command -v python3.11)
  "$py" -m venv "$CERTBOT_DIR"
  "$CERTBOT_DIR/bin/pip" install --quiet certbot certbot-dns-route53
}

install_files() {
  local live
  live=/etc/letsencrypt/live/$(cat "$HOST_FILE")
  install -d -m 700 -o tanktactics -g tanktactics "$TLS_DIR"
  install -m 644 -o tanktactics -g tanktactics "$live/fullchain.pem" "$TLS_DIR/fullchain.pem"
  install -m 600 -o tanktactics -g tanktactics "$live/privkey.pem" "$TLS_DIR/privkey.pem"
}

case "${1:-}" in
  issue)
    host=${2:?usage: tank-tactics-tls issue <host>}
    install_certbot
    echo "$host" > "$HOST_FILE"
    # No email: Let's Encrypt only uses it for expiry warnings, and the timer renews.
    "$CERTBOT" certonly --non-interactive --agree-tos --register-unsafely-without-email \
      --dns-route53 --keep-until-expiring --cert-name "$host" -d "$host" \
      --deploy-hook "/usr/local/bin/tank-tactics-tls install"
    install_files
    mkdir -p "$(dirname "$DROPIN")"
    cat > "$DROPIN" <<EOF
[Service]
Environment=PORT=$PORT SSL_CERTFILE=$TLS_DIR/fullchain.pem SSL_KEYFILE=$TLS_DIR/privkey.pem
EOF
    cat > /etc/systemd/system/tank-tactics-tls-renew.service <<EOF
[Unit]
Description=Renew the Tank Tactics origin certificate

[Service]
Type=oneshot
ExecStart=$CERTBOT renew --quiet
EOF
    cat > /etc/systemd/system/tank-tactics-tls-renew.timer <<'EOF'
[Unit]
Description=Renew the Tank Tactics origin certificate twice a day when due

[Timer]
OnCalendar=*-*-* 03,15:00:00
RandomizedDelaySec=1h
Persistent=true

[Install]
WantedBy=timers.target
EOF
    systemctl daemon-reload
    systemctl enable --now tank-tactics-tls-renew.timer
    echo "Origin TLS: https://$host:$PORT"
    ;;
  install)
    install_files
    systemctl try-restart tank-tactics
    ;;
  *)
    echo "usage: tank-tactics-tls issue <host> | install" >&2
    exit 2
    ;;
esac
