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

trap 'rm -f "$ENV_FILE.new"' EXIT
echo "GOOGLE_CLIENT_ID=$client_id" > "$ENV_FILE.new"
chmod 644 "$ENV_FILE.new"
mv "$ENV_FILE.new" "$ENV_FILE"
echo "Google sign-in: on"
