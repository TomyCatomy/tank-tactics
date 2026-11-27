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
| `up` | Creates the server and CloudFront, ships `main`, and prints the URL in the run summary (10-20 min) |
| `redeploy` | Ships `main` to the running server and keeps game data |
| `down` | Deletes the server, disk, snapshots, IP, security group and CloudFront. **Deletes all game data**; requires typing `DELETE` |

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
  so keep the site's address there by hand.
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

3. Run `redeploy` (or `up`). The run log says `Telemetry export: <endpoint>`; the token is
   never printed.

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

## Why this shape

The server is a single stateful process: games live in memory, WebSockets stay open, a
background scheduler fires AP ticks, and state goes to a SQLite file. It runs on one
always-on VM with CloudFront in front for HTTPS:

```
browser --HTTPS/WSS--> CloudFront (*.cloudfront.net, caching off)
                         --HTTP :8080--> EC2 (Elastic IP) --> uvicorn --> SQLite on EBS
```

| Piece | Details |
|---|---|
| Foundation | [`github-access.yaml`](github-access.yaml): stays in place across `up` and `down` |
| Instance | Amazon Linux 2023 (arm64), CPU credits `standard`, IMDSv2 required, 10 GB encrypted gp3 |
| Boot | [`userdata.sh`](userdata.sh): settings from SSM, Python venv, `/usr/local/bin/tank-tactics-deploy`, systemd unit `tank-tactics`, database at `/var/lib/tank-tactics/tank-tactics.db` |
| Access | No SSH or key pair; commands go through AWS Systems Manager |
| Network | Default VPC. TCP 8080 is open only to the CloudFront origin-facing prefix list |
| Backups | Daily 03:00 UTC snapshots of volumes tagged `backup=daily`, keep 7 |

Each `up` gets a **new** `*.cloudfront.net` address, so earlier invite and rejoin links stop
working. With Google sign-in on, add each new address to the OAuth client's *Authorized
JavaScript origins*.

## Cost

About $15-17/month while up with `t4g.small`: instance ~$12, public IPv4 ~$3.60, disk and
snapshots ~$1, CloudFront small. After `down`, only the tiny S3 bucket remains, normally empty.

## History

- 2026-11-20: deployed by hand, smoke-tested, then taken down. Only a test lobby was lost.
- 2026-11-23: shell scripts replaced by the GitHub Actions workflow.
