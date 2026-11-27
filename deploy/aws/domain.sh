#!/usr/bin/env bash
# Serves the site on $DOMAIN (and redirects www.$DOMAIN to it) from the CloudFront
# distribution whose comment is $COMMENT. Run by .github/workflows/aws-deploy.yml:
#   domain.sh attach   after up and redeploy; idempotent, changes only what differs
#   domain.sh detach   before down; deletes the domain's A/AAAA records
# The Route 53 zone and the us-east-1 ACM certificate are made once by hand, and the
# redirect function by github-access.yaml (see deploy/aws/README.md). If the zone or an
# issued certificate is missing, attach warns and leaves the cloudfront.net address alone.
set -euo pipefail
mode=${1:?usage: domain.sh attach|detach}
: "${DOMAIN:?}" "${COMMENT:?}"
WWW="www.$DOMAIN"
CLOUDFRONT_ZONE=Z2FDTNDATAQYW2 # the fixed hosted zone of every CloudFront alias target
FUNCTION=tank-tactics-www-redirect

summary() { echo "$1" >> "${GITHUB_STEP_SUMMARY:-/dev/null}"; }

zone=$(aws route53 list-hosted-zones-by-name --dns-name "$DOMAIN" \
  --query "HostedZones[?Name=='$DOMAIN.'].Id | [0]" --output text)
if [ "$zone" = None ]; then
  echo "::warning::No Route 53 hosted zone for $DOMAIN; the site stays on its cloudfront.net address."
  exit 0
fi

change_records() { # $1: JSON array of changes
  [ "$(jq length <<< "$1")" = 0 ] && return
  id=$(aws route53 change-resource-record-sets --hosted-zone-id "$zone" \
    --change-batch "$(jq -n --argjson c "$1" '{Changes: $c}')" --query ChangeInfo.Id --output text)
  aws route53 wait resource-record-sets-changed --id "$id"
}

if [ "$mode" = detach ]; then
  changes=$(aws route53 list-resource-record-sets --hosted-zone-id "$zone" --output json |
    jq --arg a "$DOMAIN." --arg w "$WWW." '[.ResourceRecordSets[]
      | select((.Name == $a or .Name == $w) and (.Type == "A" or .Type == "AAAA"))
      | {Action: "DELETE", ResourceRecordSet: .}]')
  change_records "$changes"
  echo "Removed the DNS records for $DOMAIN"
  exit 0
fi

cert=$(aws acm list-certificates --region us-east-1 --certificate-statuses ISSUED \
  --query "CertificateSummaryList[?DomainName=='$DOMAIN'].CertificateArn | [0]" --output text)
if [ "$cert" = None ]; then
  echo "::warning::No issued certificate for $DOMAIN in us-east-1 yet (still validating?); run redeploy later."
  exit 0
fi
read -r dist target < <(aws cloudfront list-distributions \
  --query "DistributionList.Items[?Comment=='$COMMENT' && Enabled] | [0].[Id,DomainName]" --output text)
if [ "$dist" = None ]; then
  echo "::error::No enabled CloudFront distribution to attach $DOMAIN to."; exit 1
fi
function_arn=$(aws cloudfront describe-function --name "$FUNCTION" --stage LIVE \
  --query FunctionSummary.FunctionMetadata.FunctionARN --output text)

aws cloudfront get-distribution-config --id "$dist" --output json > dist.json
jq --arg cert "$cert" --arg fn "$function_arn" --arg a "$DOMAIN" --arg w "$WWW" '.DistributionConfig
  | .Aliases = {Quantity: 2, Items: [$a, $w]}
  | .ViewerCertificate = {CloudFrontDefaultCertificate: false, ACMCertificateArn: $cert,
      SSLSupportMethod: "sni-only", MinimumProtocolVersion: "TLSv1.2_2021"}
  | .DefaultCacheBehavior.FunctionAssociations = {Quantity: 1,
      Items: [{EventType: "viewer-request", FunctionARN: $fn}]}' dist.json > dist-domain.json
# Compare only what is set above: the API also returns deprecated certificate fields.
settings='[.Aliases, (.ViewerCertificate | {ACMCertificateArn, SSLSupportMethod, MinimumProtocolVersion}),
  .DefaultCacheBehavior.FunctionAssociations]'
if jq -e --slurpfile want dist-domain.json \
    "(.DistributionConfig | $settings) == (\$want[0] | $settings)" dist.json >/dev/null; then
  echo "CloudFront already serves $DOMAIN"
else
  aws cloudfront update-distribution --id "$dist" --if-match "$(jq -r .ETag dist.json)" \
    --distribution-config file://dist-domain.json >/dev/null
  aws cloudfront wait distribution-deployed --id "$dist"
  echo "CloudFront now serves $DOMAIN and $WWW"
fi

changes=$(jq -n --arg a "$DOMAIN" --arg w "$WWW" --arg t "$target" --arg z "$CLOUDFRONT_ZONE" '
  [[$a, $w][] as $name | ["A", "AAAA"][] as $type | {Action: "UPSERT", ResourceRecordSet: {
    Name: $name, Type: $type, AliasTarget: {HostedZoneId: $z, DNSName: $t, EvaluateTargetHealth: false}}}]')
change_records "$changes"

# Check through CloudFront directly, so the result doesn't depend on DNS having propagated.
code=$(curl -s -o /dev/null -w '%{http_code}' --connect-to "$DOMAIN:443:$target:443" \
  "https://$DOMAIN/api/defaults" || true)
if [ "$code" != 200 ]; then
  echo "::error::https://$DOMAIN/api/defaults returned $code through CloudFront"; exit 1
fi
redirect=$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' --connect-to "$WWW:443:$target:443" \
  "https://$WWW/game?x=1" || true)
if [ "$redirect" != "301 https://$DOMAIN/game?x=1" ]; then
  echo "::error::https://$WWW/ should redirect to https://$DOMAIN/ but returned: $redirect"; exit 1
fi
echo "Site URL: https://$DOMAIN"
summary "### Site: https://$DOMAIN"
