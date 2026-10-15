# Deploy runbook

How BasedNews goes from a laptop to a public HTTPS site on AWS, and how to run it afterwards. How the parts fit
together: [ARCHITECTURE.md](ARCHITECTURE.md). Decisions: [ADR-010](decisions/ADR-010-deployment.md).
Principle: free until free doesn't work. Expected cost ≈ $2.50 a month for AWS plus ≤ $4.80 of AI (README, Costs).

---

## Step 1 — accounts and tools (the maintainer)

| | Item | State |
|---|---|---|
| 1.1 | OpenAI: prepaid credits, auto-reload capped at **$10 a month** (the provider-side cap behind A48) | ✅ 2026-10-12 |
| 1.1 | OpenAI: a separate project `basednews-prod` and key, made on deploy day and typed straight into SSM (step 3.2) | on deploy day |
| 1.2 | AWS: root MFA; IAM Identity Center in eu-north-1 (single-Region, no KMS key) with an `AdministratorAccess` user; Cost Explorer; a $10 budget emailing at 85 % / 100 % | ✅ 2026-10-12 |
| 1.3 | Tools: AWS CLI, Terraform, Docker Desktop; `aws configure sso` → profile `basednews`, region `eu-north-1` | ✅ 2026-10-12 |
| 1.4 | Domain: deferred; the site launches on `https://<id>.cloudfront.net` | later |
| 1.5 | Grafana Cloud (free tier) stack + an OTLP token, for 2.8 | later |
| 1.6 | GitHub: push; the `ci` run (tests, image, site, Terraform checks) is green | each push |

---

## Step 2 — what gets built (Claude, once the BACKLOG items are marked `executable: yes`)

| # | Piece | Check | State |
|---|---|---|---|
| 2.1 | Job runner `pipeline.job <step\|job>`: a record per step, JSON-line logs, the module's eval as a gate (A53) | unit tests; a failing gate stops the run | ✅ 2026-10-12 |
| 2.2 | Export: positions → JSON for the site (< 100 kB per view); slimmer animation files | payload check; the site renders it | ✅ 2026-10-12 |
| 2.3 | Retention (A52) + monthly Parquet history | dry-run report; a refit from the kept tables reproduces positions | ✅ 2026-10-12 |
| 2.4 | Dockerfile + docker compose (the same image locally) | `docker compose run --rm job list`; CI builds it | ✅ 2026-10-12 |
| 2.5 | SQLite-in-S3: lock → download → run → upload on success, single writer (A55) | tests on a fake bucket and a fake S3 client | ✅ 2026-10-12 |
| 2.6 | Terraform: `infra/bootstrap` (state bucket), `infra/main` (S3 + CloudFront, ECR, ECS Fargate arm64, Step Functions, schedules created off, alarms → email, kill switch, GitHub OIDC roles) | `validate`; a read-only `plan` (58 resources); AWS's validator on the four job definitions; checkov clean | ✅ 2026-10-12, not applied |
| 2.7 | CI deploy: image → ECR, site → S3, `terraform plan` on pull requests; never `apply` (A57) | green runs; the deploy steps wait for the repository variables | ✅ 2026-10-12 |
| 2.8 | OpenTelemetry from the job runner to Grafana | a trace per run, a span per step | not started |

---

## Step 3 — first deploy (together, ≈ 45 min)

Run from the repo root after `aws sso login --profile basednews`. Everything is created with the schedules **off**.

1. **State bucket** (once):
   ```bash
   AWS_PROFILE=basednews terraform -chdir=infra/bootstrap init
   AWS_PROFILE=basednews terraform -chdir=infra/bootstrap apply
   ```
2. **OpenAI key for production, typed in the console** (never in a shell or chat): platform.openai.com → project
   `basednews-prod` → new secret key → AWS console → Systems Manager → Parameter Store → Create parameter
   `/basednews/openai_api_key`, type **SecureString**, paste.
3. **Infrastructure:** copy `infra/main/terraform.tfvars.example` to `terraform.tfvars` (git-ignored) and set
   `alert_email`. Then:
   ```bash
   AWS_PROFILE=basednews terraform -chdir=infra/main init \
     -backend-config="bucket=$(terraform -chdir=infra/bootstrap output -raw state_bucket)"
   AWS_PROFILE=basednews terraform -chdir=infra/main plan     # read it: ≈ 58 to add, nothing changed or destroyed
   AWS_PROFILE=basednews terraform -chdir=infra/main apply
   ```
   Confirm the two subscription emails from AWS (alerts, kill switch).
4. **GitHub variables:** `terraform -chdir=infra/main output github_variables` → GitHub → Settings → Secrets and
   variables → Actions → **Variables** → add the six. None is secret.
5. **Push to main** → CI pushes the image (`:<commit>` and `:prod`) and uploads the site. Check the Actions run.
6. **Seed the database** (once, never overwrites):
   ```bash
   BASEDNEWS_STATE=$(terraform -chdir=infra/main output -raw state_url) AWS_PROFILE=basednews \
     uv run python -m pipeline.job push-db
   ```
7. **One refresh by hand** (embeddings ≈ $0.01): Step Functions → `basednews-refresh` → Start execution (or the CLI
   line under "Run a job now" below). Read the four step records and the logs (see "What ran").
8. **Open the site:** `terraform -chdir=infra/main output site_url`.
9. **Schedules on:** in `terraform.tfvars`, then `apply`:
   - `refresh_enabled = true`: hourly data and the daily feed check;
   - `judge_enabled = true`: spends up to `daily_budget_usd` ($0.15) a day — **needs the maintainer's go**;
   - `maintenance_enabled` (retention `--apply`) and `publish_positions` (A54) are separate decisions.

---

## Running it (day two onwards)

Set once per shell: `export AWS_PROFILE=basednews` and
`P=$(terraform -chdir=infra/main output -raw state_url)` (the private bucket's `prod/` prefix).

| Task | How |
|---|---|
| **Run a job now** | `aws stepfunctions start-execution --state-machine-arn $(terraform -chdir=infra/main output -json state_machines \| jq -r .refresh)` (`refresh`, `feeds`, `judge`, `maintenance`) |
| **What ran, what failed** | `aws s3 ls $P/runs/` → `aws s3 cp $P/runs/<execution>/<step>.json -` (commit, counts, gate numbers, error, traceback) |
| **The logs of a run** | CloudWatch → Logs Insights → `/basednews/jobs`: `fields @timestamp, step, event, status, error \| filter run_id = "<execution>" \| sort @timestamp` |
| **Production data, locally** | `aws s3 cp $P/db/basednews.sqlite /tmp/prod.sqlite` then `BASEDNEWS_DB=/tmp/prod.sqlite uv run python -m pipeline.job history` (a copy; nothing goes back) |
| **Ship code** | push to main: CI moves `:prod`; the next task runs it. Every record names its commit |
| **Change infrastructure** | edit `infra/`, open a pull request (CI shows the plan), merge, then `terraform apply` by hand (A57) |
| **A job on or off, a budget** | `*_enabled`, `daily_budget_usd` in `terraform.tfvars` → `apply` |
| **Roll the database back** | schedules off first; S3 console → `prod/db/basednews.sqlite` → Versions → restore an earlier one (≈ 1 day of versions are kept) |
| **Rotate the OpenAI key** | new key in OpenAI → overwrite `/basednews/openai_api_key` in Parameter Store → the next task uses it → revoke the old key |
| **Query the history lake** | `aws s3 sync $P/lake data/lake-prod`, then `uv run --with duckdb python -c "import duckdb; print(duckdb.sql(\"select count(*) from read_parquet('data/lake-prod/judge_calls/*/*.parquet', hive_partitioning=true)\"))"` |
| **Check the bill** | Cost Explorer, grouped by service; compare with README → Costs |

## What the job container gets (task definition)
| Variable | Value | Why |
|---|---|---|
| `BASEDNEWS_STATE` | `s3://<private-bucket>/prod` | the database, step records (`runs/`), the lake (`lake/`) and the lock |
| `BASEDNEWS_SITE_BUCKET` | `s3://<site-bucket>/data` | where the export's JSON goes |
| `BASEDNEWS_PUBLISH_POSITIONS` | `0` or `1` | A54: whether the latest fit is public |
| `BASEDNEWS_RUN_BUDGET_USD` | `0.15` (judge job only) | a paid step refuses to run without it (A48) |
| `OPENAI_API_KEY` | from SSM `/basednews/openai_api_key` | never in the image or the task definition's plain text |
| `BASEDNEWS_COMMIT` | baked into the image at build | every record names the code version |

The command is `<step> --run-id <execution name>`. Exit codes: 0 ok · 1 step failed · 2 gate failed · 3 refused
(configuration) · 4 the stored database changed meanwhile · 5 another job held the lock for 45 minutes (A55).

## What heals itself (ADR-012)
| Failure | What happens, without anyone |
|---|---|
| a step crashes, runs out of memory or is killed | its lease stops being renewed; the step is retried after 2 min and takes the lock over after 5 min; the stored database is unchanged |
| a task is stopped (StopTask, timeout) | it ends cleanly: record written, nothing uploaded, lease released |
| a task hangs | it stops renewing after 80 min and is stopped at 90; the next job takes over |
| a broken image | the CI smoke test (`selftest`) keeps it from becoming `prod`; if one gets through, the runs fail and the 3-hour alarm says so |
| a single failed refresh | no email: the next run (or the retry) repairs it |

## When an alarm email comes
An email now means it did **not** heal: no successful refresh for 3 hours, or a daily job failed after its retry.
1. "refresh unhealthy": Step Functions console → the latest failed execution → its step → logs. The step's record
   (`runs/<execution>/<step>.json`) has the commit, the counts, the gate's numbers and the traceback.
   - **The gate's numbers are odd** (e.g. a source outage): it waits for the source to come back; nothing to do.
   - **A code error**: fix, push. CI tests, smoke-tests and ships it.
2. You never delete `db/LOCK`: a dead job's lease expires by itself within 5 minutes.
Failure modes in more depth: [ARCHITECTURE.md → When something breaks](ARCHITECTURE.md#when-something-breaks).

## When the money brake fires
If the month's AWS cost passes `killswitch_usd` ($15), the kill-switch Lambda switches every schedule off and the
budget emails the maintainer. Nothing is deleted. Find the cause in Cost Explorer, then `terraform apply` turns the
schedules back on. The $10 console budget warns before that; OpenAI's reload limit and the app's monthly cap (A48)
limit AI spend separately.
