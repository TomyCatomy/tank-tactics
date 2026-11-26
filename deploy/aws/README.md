# Deploying Tank Tactics on AWS

Deployment runs from a GitHub Actions workflow,
[`.github/workflows/aws-deploy.yml`](../../.github/workflows/aws-deploy.yml). Start `up`,
`redeploy` or `down` from **Actions > AWS deploy > Run workflow**, on `main`.

One server runs two environments, each with its own site, game database and sign-ins:

| Environment | Site | Gets new code | Debug options |
|---|---|---|---|
| `staging` | CloudFront distribution "Tank Tactics staging" | Automatically, on every push to `main` that changes shipped files | On (`ALLOW_DEBUG=true`, e.g. `debug.fast_ticks`) |
| `production` | CloudFront distribution "Tank Tactics" | Only when you run `redeploy` with environment `production` | Off |

A push to `main` that changes `tank_tactics/`, `public/` or `requirements.txt` redeploys
staging if the server is up, and skips cleanly if it is not. To release, try the change on
staging, then run `redeploy` with environment `production`. That ships the current `main`,
so if more commits landed after the staging deploy, they ship too. Each redeploy restarts
only that environment, so its connected players briefly reconnect. Runs are queued one at a time;
if several pushes wait, only the newest is deployed.

| Action | What it does |
|---|---|
| `up` | Creates the server and both CloudFront sites, ships `main` to both, and prints both URLs in the run summary (10-20 min) |
| `redeploy` | Ships `main` to the chosen environment (default `staging`) and keeps game data. Also sets that environment up if the server predates it |
| `down` | Deletes the server, disk, snapshots, IP, security group and both CloudFront sites. **Deletes all game data of both environments**; requires typing `DELETE` |

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

## Why this shape

The server is a single stateful process: games live in memory, WebSockets stay open, a
background scheduler fires AP ticks, and state goes to a SQLite file. Both environments run
on one always-on VM, each behind its own CloudFront distribution for HTTPS:

```
browser --HTTPS/WSS--> CloudFront "Tank Tactics"         --HTTP :8080--> EC2 --> tank-tactics         --> SQLite
browser --HTTPS/WSS--> CloudFront "Tank Tactics staging" --HTTP :8081--> EC2 --> tank-tactics-staging --> SQLite
```

Sharing the VM saves a second instance (~$15/month). [`install-env.sh`](install-env.sh)
keeps the environments apart on it: each has its own systemd unit, system user, venv, code
and database directory (mode 700), so staging can't read production's games. Staging is
capped at half a vCPU and 512 MB with a low CPU weight, so it can't starve production or
drain the instance's CPU credits. Don't run heavy load such as `scripts/simulate.py`
there anyway. What staging can't test is the server itself: a change to the boot script,
Python version or instance type reaches both environments at once. Move staging to its own
instance once production has players who would notice an outage.

| Piece | Details |
|---|---|
| Foundation | [`github-access.yaml`](github-access.yaml): stays in place across `up` and `down` |
| Instance | Amazon Linux 2023 (arm64), CPU credits `standard`, IMDSv2 required, 10 GB encrypted gp3 |
| Boot | [`userdata.sh`](userdata.sh): settings from SSM, Python, then [`install-env.sh`](install-env.sh) (as `/usr/local/bin/tank-tactics-install <environment>`) for each environment |
| Environments | `production`: unit `tank-tactics`, port 8080, database `/var/lib/tank-tactics/tank-tactics.db`. `staging`: unit `tank-tactics-staging`, port 8081, database `/var/lib/tank-tactics-staging/tank-tactics.db` |
| Access | No SSH or key pair; commands go through AWS Systems Manager |
| Network | Default VPC. TCP 8080 and 8081 are open only to the CloudFront origin-facing prefix list |
| Backups | Daily 03:00 UTC snapshots of volumes tagged `backup=daily`, keep 7 |

Each `up` gets **new** `*.cloudfront.net` addresses, so earlier invite and rejoin links stop
working. With Google sign-in on, add each new address (production and staging) to the OAuth
client's *Authorized JavaScript origins*. Both environments use the same client ID.

## Cost

About $15-17/month while up with `t4g.small`, staging included: instance ~$12, public IPv4
~$3.60, disk and snapshots ~$1, CloudFront small. After `down`, only the tiny S3 bucket remains, normally empty.

## History

- 2026-11-20: deployed by hand, smoke-tested, then taken down. Only a test lobby was lost.
- 2026-11-23: shell scripts replaced by the GitHub Actions workflow.
- 2026-11-26: staging environment added on the same server; pushes to `main` now deploy
  staging, and production is deployed by hand.
