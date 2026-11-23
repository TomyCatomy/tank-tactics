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
   - `GOOGLE_CLIENT_ID` (optional): turns on Google sign-in.

3. Merge the workflow to `main`. GitHub only offers **Run workflow** for workflows on the
   default branch.

No AWS keys are stored in GitHub. The job gets one-hour credentials through OIDC, and the
role trusts only `main` of this repository. It has no IAM write access, can pass only the
instance role, can launch only `t4g.small` or `t4g.micro`, and can change or delete only
EC2 resources tagged `app=tank-tactics`.

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
| Boot | [`userdata.sh`](userdata.sh): Python venv, `/usr/local/bin/tank-tactics-deploy`, systemd unit `tank-tactics`, database at `/var/lib/tank-tactics/tank-tactics.db` |
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
