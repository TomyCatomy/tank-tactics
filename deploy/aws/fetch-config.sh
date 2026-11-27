#!/bin/bash
# Writes the server's environment file from SSM Parameter Store. Run as root on the server.
# The AWS deploy workflow installs this as /usr/local/bin/tank-tactics-config and runs it on
# first boot and before every redeploy. The instance role reads parameters through its
# AmazonSSMManagedInstanceCore policy.
#
# /tank-tactics/google-client-id (required): OAuth client ID for "Sign in with Google".
# The deployed server always has sign-in on, so if the parameter can't be read, or Google
# doesn't recognise it as a real client, this exits non-zero, leaves the current file
# alone, and the deploy fails before restarting anything.
#
# /tank-tactics/otel-endpoint (String) and /tank-tactics/otel-headers (SecureString),
# optional: OTLP export to Grafana Cloud. With neither, telemetry is off; with only one,
# or an endpoint that isn't https, the deploy fails the same way. The headers hold an API
# token, so they are never printed and the file is readable by root only.
#
# /tank-tactics/discord-client-id (String) and /tank-tactics/discord-client-secret
# (SecureString), optional: the Discord app behind the lobby's "Connect to Discord"
# button. With neither the button is hidden; with only one the deploy fails. The secret
# is never printed either.
# /tank-tactics/database-url and /tank-tactics/database-url-unpooled (SecureString),
# optional: Neon's pooled and direct Postgres URLs (`neon env pull` writes them as
# DATABASE_URL and DATABASE_URL_UNPOOLED). With neither, the server keeps its SQLite file;
# with only one, or values that aren't a pooled and a direct postgres URL, the deploy fails.
# They hold the database password, so only the host is printed.
set -euo pipefail

REGION=eu-north-1
PARAMETER=/tank-tactics/google-client-id
ENV_FILE=/etc/tank-tactics.env

if ! client_id=$(aws ssm get-parameter --region "$REGION" --name "$PARAMETER" \
    --query Parameter.Value --output text); then
  echo "Cannot read required SSM parameter $PARAMETER; Google sign-in must be on." >&2
  exit 1
fi
if ! [[ "$client_id" =~ ^[A-Za-z0-9._-]+\.apps\.googleusercontent\.com$ ]]; then
  echo "$PARAMETER is not a Google OAuth client ID (<id>.apps.googleusercontent.com)." >&2
  exit 1
fi

# Ask Google whether the client exists. Start a sign-in with a redirect URI the client
# can't have registered: Google answers an unknown or deleted client with invalid_client,
# and a real one with redirect_uri_mismatch. The error code is the first field of the
# base64url-encoded authError in the redirect. Anything else, including Google being
# unreachable, counts as unverified and fails.
google_error() {
  local location err
  location=$(curl -sS --max-time 20 -o /dev/null -w '%{redirect_url}' -G \
    https://accounts.google.com/o/oauth2/v2/auth \
    --data-urlencode "client_id=$1" --data-urlencode redirect_uri=https://example.com/oauth-check \
    --data-urlencode response_type=id_token --data-urlencode scope=openid \
    --data-urlencode nonce=check) || return 0
  err=$(sed -n 's/.*[?&]authError=\([^&]*\).*/\1/p' <<<"$location" | tr '_-' '/+')
  while (( ${#err} % 4 )); do err+='='; done
  base64 -d <<<"$err" 2>/dev/null | head -c 64 | tr -c 'a-z_' '\n' | grep -m1 . || true
}
verdict=$(google_error "$client_id")
if [[ "$verdict" != redirect_uri_mismatch ]]; then
  echo "Google did not accept $PARAMETER as a real OAuth client (got: ${verdict:-no answer})." >&2
  exit 1
fi

# get-parameters lists missing names under InvalidParameters instead of failing.
if ! otel=$(aws ssm get-parameters --region "$REGION" --with-decryption --output text \
    --names /tank-tactics/otel-endpoint /tank-tactics/otel-headers \
    --query '[Parameters[?Name==`/tank-tactics/otel-endpoint`].Value|[0],
              Parameters[?Name==`/tank-tactics/otel-headers`].Value|[0]]'); then
  echo "Cannot read the /tank-tactics/otel-* SSM parameters." >&2
  exit 1
fi
IFS=$'\t' read -r otel_endpoint otel_headers <<<"$otel"
[[ "$otel_endpoint" == None ]] && otel_endpoint=""
[[ "$otel_headers" == None ]] && otel_headers=""
if [[ -n "$otel_endpoint" || -n "$otel_headers" ]]; then
  if [[ -z "$otel_endpoint" || -z "$otel_headers" ]]; then
    echo "Set both /tank-tactics/otel-endpoint and /tank-tactics/otel-headers, or neither." >&2
    exit 1
  fi
  if ! [[ "$otel_endpoint" =~ ^https://[^[:space:]]+$ ]]; then
    echo "/tank-tactics/otel-endpoint must be an https:// URL." >&2
    exit 1
  fi
fi

if ! discord=$(aws ssm get-parameters --region "$REGION" --with-decryption --output text     --names /tank-tactics/discord-client-id /tank-tactics/discord-client-secret     --query '[Parameters[?Name==`/tank-tactics/discord-client-id`].Value|[0],
              Parameters[?Name==`/tank-tactics/discord-client-secret`].Value|[0]]'); then
  echo "Cannot read the /tank-tactics/discord-* SSM parameters." >&2
  exit 1
fi
IFS=$'	' read -r discord_id discord_secret <<<"$discord"
[[ "$discord_id" == None ]] && discord_id=""
[[ "$discord_secret" == None ]] && discord_secret=""
if [[ -n "$discord_id" || -n "$discord_secret" ]]; then
  if [[ -z "$discord_id" || -z "$discord_secret" ]]; then
    echo "Set both /tank-tactics/discord-client-id and /tank-tactics/discord-client-secret, or neither." >&2
    exit 1
  fi
  if ! [[ "$discord_id" =~ ^[0-9]{15,25}$ && "$discord_secret" =~ ^[A-Za-z0-9_-]+$ ]]; then
    echo "The /tank-tactics/discord-* parameters don't look like a Discord client ID and secret." >&2
    exit 1
  fi
fi

if ! db=$(aws ssm get-parameters --region "$REGION" --with-decryption --output text \
    --names /tank-tactics/database-url /tank-tactics/database-url-unpooled \
    --query '[Parameters[?Name==`/tank-tactics/database-url`].Value|[0],
              Parameters[?Name==`/tank-tactics/database-url-unpooled`].Value|[0]]'); then
  echo "Cannot read the /tank-tactics/database-url* SSM parameters." >&2
  exit 1
fi
IFS=$'\t' read -r db_url db_url_unpooled <<<"$db"
[[ "$db_url" == None ]] && db_url=""
[[ "$db_url_unpooled" == None ]] && db_url_unpooled=""
db_host=""
if [[ -n "$db_url" || -n "$db_url_unpooled" ]]; then
  if [[ -z "$db_url" || -z "$db_url_unpooled" ]]; then
    echo "Set both /tank-tactics/database-url and /tank-tactics/database-url-unpooled, or neither." >&2
    exit 1
  fi
  # postgres[ql]://user:password@host[:port]/db?options; the pooled host has -pooler in it.
  url_re='^postgres(ql)?://[^[:space:]@/]+@([^[:space:]/:@]+)(:[0-9]+)?/[^[:space:]]+$'
  if ! [[ "$db_url" =~ $url_re && "${BASH_REMATCH[2]}" == *-pooler.* ]]; then
    echo "/tank-tactics/database-url must be Neon's pooled postgres URL (host with -pooler)." >&2
    exit 1
  fi
  db_host=${BASH_REMATCH[2]}
  if ! [[ "$db_url_unpooled" =~ $url_re && "${BASH_REMATCH[2]}" != *-pooler.* ]]; then
    echo "/tank-tactics/database-url-unpooled must be Neon's direct postgres URL (host without -pooler)." >&2
    exit 1
  fi
fi

trap 'rm -f "$ENV_FILE.new"' EXIT
install -m 600 /dev/null "$ENV_FILE.new"
echo "GOOGLE_CLIENT_ID=$client_id" > "$ENV_FILE.new"
if [[ -n "$otel_endpoint" ]]; then
  {
    echo "OTEL_EXPORTER_OTLP_ENDPOINT=$otel_endpoint"
    echo "OTEL_EXPORTER_OTLP_HEADERS=$otel_headers"
    echo "OTEL_RESOURCE_ATTRIBUTES=deployment.environment.name=production"
  } >> "$ENV_FILE.new"
fi
if [[ -n "$discord_id" ]]; then
  {
    echo "DISCORD_CLIENT_ID=$discord_id"
    echo "DISCORD_CLIENT_SECRET=$discord_secret"
  } >> "$ENV_FILE.new"
fi
if [[ -n "$db_url" ]]; then
  {
    echo "DATABASE_URL=$db_url"
    echo "DATABASE_URL_UNPOOLED=$db_url_unpooled"
  } >> "$ENV_FILE.new"
fi
mv "$ENV_FILE.new" "$ENV_FILE"
echo "Google sign-in: on"
echo "Telemetry export: ${otel_endpoint:-off}"
echo "Discord connect: $([[ -n "$discord_id" ]] && echo on || echo off)"
echo "Database: ${db_host:+Postgres at $db_host}${db_host:-SQLite file}"
