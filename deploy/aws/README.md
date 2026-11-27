# Deploying Tank Tactics on AWS

Deployment runs from a GitHub Actions workflow,
[`.github/workflows/aws-deploy.yml`](../../.github/workflows/aws-deploy.yml). Start `up`,
`redeploy` or `down` from **Actions > AWS deploy > Run workflow**, on `main`.

A push to `main` that changes `tank_tactics/`, `public/` or `requirements.txt` runs
`redeploy` automatically if the server is up, and skips cleanly if it is not. Each redeploy
restarts the app, so connected players briefly reconnect. Runs are queued one at a time;
if several pushes wait, only the newest is deployed.

| Action | What it does |
|---|---|
| `up` | Creates the server and CloudFront, ships `main`, points the domain at it, and prints the URL in the run summary (10-20 min) |
| `redeploy` | Ships `main` to the running server, keeps game data, and attaches the domain if it isn't yet |
| `down` | Deletes the server, disk, snapshots, IP, security group, CloudFront and the domain's DNS records. **Deletes all game data**; requires typing `DELETE` |

## One-time setup

1. Create the permanent foundation (GitHub access, instance role, code bucket, snapshot policy):

   ```bash
   aws login --profile tank-tactics
   aws cloudformation deploy --profile tank-tactics --region eu-north-1 \
     --stack-name tank-tactics-github --template-file deploy/aws/github-access.yaml \
     --capabilities CAPABILITY_NAMED_IAM
   aws cloudformation describe-stacks --profile tank-tactics --region eu-north-1 \
     --stack-name tank-tactics-github --query 'Stacks[0].Outputs[0].OutputValue' --output text
   ```

2. In GitHub, under **Settings > Secrets and variables > Actions > Variables**, add:
   - `AWS_ROLE_ARN`: the value printed above.

3. Merge the workflow to `main`. GitHub only offers **Run workflow** for workflows on the
   default branch.

No AWS keys are stored in GitHub. The job gets one-hour credentials through OIDC, and the
role trusts only `main` of this repository. It has no IAM write access, can pass only the
instance role, can launch only `t4g.small` or `t4g.micro`, and can change or delete only
EC2 resources tagged `app=tank-tactics`.

## Domain: tanktactics.io

The site is served at **https://tanktactics.io**; `www.tanktactics.io` redirects there
(301, path and query kept) so every player shares one origin, and with it the seats the
browser remembers. The `*.cloudfront.net` address keeps working too.

These pieces are permanent and survive `down`:

- **Registration** stays at Spaceship, whose nameservers for the domain are set to the
  Route 53 zone's four `awsdns` servers.
- **Route 53 hosted zone** `tanktactics.io` (about $0.50/month), made by hand. It also holds
  the certificate's two `_….acm-validations.aws` CNAMEs; leave them, or renewal fails.
- **ACM certificate** for `tanktactics.io` and `www.tanktactics.io` in **us-east-1** (CloudFront
  only uses certificates from there), DNS-validated and renewed by AWS.
- **Redirect function** `tank-tactics-www-redirect`, in [`github-access.yaml`](github-access.yaml).

[`domain.sh`](domain.sh) does the per-deployment part: after `up` and every `redeploy` it adds
the domain and certificate to the distribution (only if they differ) and upserts `A`/`AAAA`
alias records for both names, then checks the site and the redirect through CloudFront.
Before `down` it deletes those records. The deploy role may change only those four records.
If the zone or an issued certificate is missing, it warns and the site stays on its
`cloudfront.net` address.

To rebuild the permanent pieces, for example on a new domain (also change `DOMAIN` in the
workflow and the stack's `Domain` parameter):

```bash
aws route53 create-hosted-zone --profile tank-tactics --name <domain> --caller-reference "$(date +%s)"
aws acm request-certificate --profile tank-tactics --region us-east-1 --validation-method DNS   --domain-name <domain> --subject-alternative-names <domain> www.<domain>
# then add the CNAMEs from `aws acm describe-certificate` to the zone, and set the zone's
# nameservers at the registrar
```

## Google sign-in (required)

The deployed server always has Google sign-in on. It reads its OAuth client ID from SSM
Parameter Store, not from GitHub, and the parameter must exist before `up` or `redeploy`:

```bash
aws ssm put-parameter --profile tank-tactics --region eu-north-1 --type String \
  --name /tank-tactics/google-client-id --value '<client id>.apps.googleusercontent.com'
```

[`fetch-config.sh`](fetch-config.sh), installed as `/usr/local/bin/tank-tactics-config`,
writes it to `/etc/tank-tactics.env` on first boot and before every `redeploy`, so a change
takes effect on the next `redeploy`. The client ID is public, so a plain `String` is fine;
the instance role reads it through `AmazonSSMManagedInstanceCore`.

The deployment fails if sign-in can't be turned on:

- Before writing it, the server asks Google whether the client exists (an unknown or
  deleted client gets `invalid_client`). It can't check *Authorized JavaScript origins*,
  so keep `https://tanktactics.io` there by hand (`www` redirects, so it needs no entry).
- `redeploy`: if the parameter is missing, malformed, unreadable or not a real client, or
  Google can't be reached, the run fails before the
  restart and the old version keeps running. It also fails if the restarted server's
  `/api/auth/config` has no client ID.
- `up`: boot stops before the game starts, and the run fails its health check. It also
  fails if `/api/auth/config` through CloudFront has no client ID.

## Telemetry: Grafana Cloud (optional)

The server can export traces, metrics and logs over OTLP to Grafana Cloud's free tier
(see [`tank_tactics/telemetry.py`](../../tank_tactics/telemetry.py) for what is sent; no
tokens, names or chat text). It is off unless both parameters below exist.

1. In Grafana Cloud, open your stack, then **Connections > Add new connection >
   OpenTelemetry (OTLP)**. Create a token there, and copy the two values it shows:
   `OTEL_EXPORTER_OTLP_ENDPOINT` (like `https://otlp-gateway-prod-<region>.grafana.net/otlp`)
   and `OTEL_EXPORTER_OTLP_HEADERS` (like `Authorization=Basic%20<base64>`).
2. Store them in SSM. The headers hold the token, so they go in a `SecureString`:

   ```bash
   aws ssm put-parameter --profile tank-tactics --region eu-north-1 --type String \
     --name /tank-tactics/otel-endpoint --value '<OTEL_EXPORTER_OTLP_ENDPOINT>'
   aws ssm put-parameter --profile tank-tactics --region eu-north-1 --type SecureString \
     --name /tank-tactics/otel-headers --value '<OTEL_EXPORTER_OTLP_HEADERS>'
   ```

3. Run `redeploy` (or `up`, which reads them at first boot). A `redeploy` log says
   `Telemetry export: <endpoint>` (or `off`); the token is never printed.

[`fetch-config.sh`](fetch-config.sh) writes them to `/etc/tank-tactics.env`, which is now
readable by root only (systemd reads it before dropping to the service user). The instance
role needs no change: `AmazonSSMManagedInstanceCore` can read parameters, and the default
`aws/ssm` KMS key lets account principals decrypt through SSM. Setting only one of the two,
or a non-https endpoint, fails the deploy before the restart. To turn telemetry off, delete
both parameters and `redeploy`.

In Grafana, the service is `tank-tactics` (environment `production`): traces in Tempo,
logs in Loki, and metrics in Prometheus as `tank_tactics_games`, `tank_tactics_websockets`,
`tank_tactics_game_events_total` and the `http_server_*` request metrics. At this game's
traffic (about 1,000 players) it uses a small part of the free tier, and egress to
Grafana stays within AWS's free 100 GB a month.


## Connect to Discord (optional)

The lobby's **Connect to Discord** button lets a host pick a Discord server and channel
instead of pasting a webhook URL (see [`discord_oauth.py`](../../tank_tactics/api/discord_oauth.py)).
It needs a Discord application, and it is off (the button is hidden) unless both
parameters below exist.

1. In the [Discord Developer Portal](https://discord.com/developers/applications), create
   an application. Under **OAuth2 > Redirects**, add
   `https://tanktactics.io/api/discord/callback` (and
   `http://localhost:8080/api/discord/callback` for local testing). It needs no bot user.
2. Store its **Client ID** and **Client Secret** in SSM. The secret goes in a `SecureString`:

   ```bash
   aws ssm put-parameter --profile tank-tactics --region eu-north-1 --type String      --name /tank-tactics/discord-client-id --value '<client id>'
   aws ssm put-parameter --profile tank-tactics --region eu-north-1 --type SecureString      --name /tank-tactics/discord-client-secret --value '<client secret>'
   ```

3. Run `redeploy`. Its log says `Discord connect: on`; the secret is never printed.

Setting only one of the two fails the deploy before the restart. To turn it off, delete
both and `redeploy`; games that already have a webhook keep posting.
## Database: Neon Postgres (optional)

By default the server keeps its games in a SQLite file on the instance's disk. With both
parameters below, it uses the Neon Postgres project instead: `Tank tactics prod`
(`autumn-mouse-33994899`, branch `production`), in `aws-eu-central-1` (Frankfurt), the
Neon region closest to this server. The repo's Neon config is in [`neon/`](../../neon/README.md).

1. From the repo root, with the Neon CLI signed in (`neon login`) and the repo linked
   (`neon link --project-id autumn-mouse-33994899 --branch production`), run
   `neon deploy --config neon/neon.ts`. It writes `DATABASE_URL` (pooled, host with
   `-pooler`) and `DATABASE_URL_UNPOOLED` (direct) to `.env.local`.
2. Store both in SSM. They hold the database password, so both are `SecureString`s:

   ```bash
   aws ssm put-parameter --profile tank-tactics --region eu-north-1 --type SecureString \
     --name /tank-tactics/database-url --value '<DATABASE_URL>'
   aws ssm put-parameter --profile tank-tactics --region eu-north-1 --type SecureString \
     --name /tank-tactics/database-url-unpooled --value '<DATABASE_URL_UNPOOLED>'
   ```

   Add `--overwrite` to replace existing values, for example after resetting the
   password in Neon.
3. Run `redeploy`. Its log says `Database: Postgres at <host>` (or `SQLite file`); the
   password is never printed.

Setting only one of the two, or values that aren't Neon's pooled and direct postgres URLs,
fails the deploy before the restart. Switching moves no data: games already in the SQLite
file stay there and aren't loaded, and switching back (delete both parameters and
`redeploy`) brings them back. The server creates its tables in Neon on first start.

## Why this shape

The server is a single stateful process: games live in memory, WebSockets stay open, a
background scheduler fires AP ticks, and state goes to a SQLite file. It runs on one
always-on VM with CloudFront in front for HTTPS:

```
browser --HTTPS/WSS--> CloudFront (tanktactics.io, caching off)
                         --HTTP :8080--> EC2 (Elastic IP) --> uvicorn --> SQLite on EBS
```

| Piece | Details |
|---|---|
| Foundation | [`github-access.yaml`](github-access.yaml): stays in place across `up` and `down` |
| Instance | Amazon Linux 2023 (arm64), CPU credits `standard`, IMDSv2 required, 10 GB encrypted gp3 |
| Boot | [`userdata.sh`](userdata.sh): settings from SSM, Python venv, `/usr/local/bin/tank-tactics-deploy`, systemd unit `tank-tactics`, database at `/var/lib/tank-tactics/tank-tactics.db`, or in Neon Postgres when the `/tank-tactics/database-url*` parameters exist |
| Access | No SSH or key pair; commands go through AWS Systems Manager |
| Network | Default VPC. TCP 8080 is open only to the CloudFront origin-facing prefix list |
| Backups | Daily 03:00 UTC snapshots of volumes tagged `backup=daily`, keep 7 |

Each `up` gets a **new** `*.cloudfront.net` address, but `tanktactics.io` follows it, so links
on the domain keep working across `down` and `up` (game data does not).

## Cost

About $15-17/month while up with `t4g.small`: instance ~$12, public IPv4 ~$3.60, disk and
snapshots ~$1, CloudFront small, Route 53 zone $0.50. After `down`, the Route 53 zone and the
tiny S3 bucket (normally empty) remain. The certificate is free; the domain is billed by Spaceship.

## History

- 2026-11-20: deployed by hand, smoke-tested, then taken down. Only a test lobby was lost.
- 2026-11-23: shell scripts replaced by the GitHub Actions workflow.
- 2026-11-27: domain `tanktactics.io` added (DNS moved from Spaceship to Route 53).
