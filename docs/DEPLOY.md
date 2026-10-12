# Deploy runbook

How BasedNews goes from a laptop to a public HTTPS site on AWS. Design and costs: [ADR-010](decisions/ADR-010-deployment.md).
Principle: free until free doesn't work. Expected cost ≈ $4–7/month plus AI spend (capped).

---

## Step 1 — accounts and tools (the maintainer, ≈ 1–1.5 h, no code needed)

**1.1 OpenAI**
- [x] Prepaid credits with auto-reload capped by a **$10 monthly reload limit** — the provider-side cap behind A48
      (checked 2026-10-12).
- [ ] On deploy day: a separate project + key for production (`basednews-prod`), so the local and production keys
      can be revoked independently. Created then and typed straight into SSM (step 3.2).

**1.2 AWS account** (personal email and card) — done 2026-10-12 (existing personal account)
- [x] Sign up at aws.amazon.com. New accounts get starter credits — check the current free-tier terms when signing up.
- [x] Root user: **turn on MFA right away**, then stop using root.
- [x] IAM Identity Center (region **eu-north-1**, single-Region instance, no KMS key): enable it, create your user, give it the `AdministratorAccess`
      permission set on this account. All CLI work uses this user, never root.
- [x] Billing: turn on Cost Explorer and create an **AWS Budget**: monthly cost budget of $10, email at 80 % actual and
      100 % forecast.

**1.3 Tools on the Mac**
- [x] `brew install awscli` and `brew tap hashicorp/tap && brew install hashicorp/tap/terraform`
- [x] Docker Desktop (free for personal use) or OrbStack, to build and run the job image locally.
- [x] `aws configure sso` → profile name `basednews`, region `eu-north-1`; check with
      `aws sts get-caller-identity --profile basednews`.

**1.4 Domain** — deferred 2026-10-12: launch on the free CloudFront address (`https://<id>.cloudfront.net`); add a
  domain later without other changes.
- [ ] Later: pick a name and register it in Route 53 (Registered domains → Register). Price depends on the ending (.com is
      roughly $15/year); registration creates the hosted zone ($0.50/month). Confirm the verification email.

**1.5 Grafana Cloud** (free tier)
- [ ] Create an account and a stack in an EU region.
- [ ] Create an access-policy token that can write metrics, logs and traces; note the **OTLP endpoint** and
      **instance ID**. Keep the token out of the repo (step 3.2).

**1.6 GitHub**
- [ ] Push the current work. The `ci` workflow (tests, lint, site build) runs on every push to `main`; check that the
      Actions tab is green.

---

## Step 2 — what gets built (Claude, once the BACKLOG items are marked `executable: yes`)

No AWS is needed for 2.1–2.5; each comes with its own check.

| # | Piece | Check | State |
|---|---|---|---|
| 2.1 | Job runner `pipeline.job <step\|job>`: a `job_runs` record per step (commit, counts, gate numbers, error), JSON-line logs, the module eval as a gate (A53) | unit tests; a failing gate stops the run | ✅ 2026-10-12 |
| 2.2 | Export step: positions → JSON for the site (< 100 kB per view); slim the animation files | payload size check; site renders from the export | ✅ 2026-10-12 (2.6 kB; animations 67 kB) |
| 2.3 | Retention (A52) + monthly Parquet history | dry-run report; refit from kept tables reproduces positions | ✅ 2026-10-12 |
| 2.4 | Dockerfile + docker compose (the same image runs locally) | `docker compose run --rm job list`; CI builds the image | ✅ 2026-10-12 (built and run locally) |
| 2.5 | SQLite-in-S3 wrapper: download → run → upload on success, single writer (A55) | tests against a fake bucket and a fake S3 client | ✅ 2026-10-12 |
| 2.6 | Terraform `infra/bootstrap` (state bucket, GitHub OIDC role) and `infra/main` (S3 + CloudFront on its default address, ECR, ECS + task definitions, Step Functions, EventBridge schedules, SSM, CloudWatch alarms, Budgets) | `terraform plan` is clean; checkov/tflint in CI |
| 2.7 | CI deploy: image → ECR, site → S3, `terraform plan` on pull requests | the workflow runs green |
| 2.8 | OpenTelemetry from the job runner to Grafana | one trace per run, one span per step |

---

## Step 3 — first deploy (together)

1. `terraform -chdir=infra/bootstrap apply` → state bucket and the role GitHub Actions uses (no stored AWS keys).
2. **Secrets, typed by the maintainer in the AWS console** (Systems Manager → Parameter Store → SecureString), not in
   a shell, so they never land in shell history: `/basednews/openai_api_key`, `/basednews/grafana_otlp_token`.
3. `terraform -chdir=infra/main plan`, read it, then `apply`.
4. Upload the current hot database to the private bucket (one time, never overwrites):
   `BASEDNEWS_STATE=s3://<private-bucket>/prod AWS_PROFILE=basednews uv run python -m pipeline.job push-db`.
5. Push to `main` → CI builds the image into ECR and the site into S3.
6. Start one **refresh-only** run by hand in Step Functions (no paid steps). Read the `runs` rows and the Grafana
   trace.
7. Open the CloudFront address over HTTPS.
8. Turn on the schedules: hourly refresh; weekly measure with a budget — **the measure schedule needs the maintainer's
   go**, because it spends money.

## What the job container gets (task definition, 2.6)
| Variable | Value | Why |
|---|---|---|
| `BASEDNEWS_STATE` | `s3://<private-bucket>/prod` | the database, step records (`runs/`) and the lake (`lake/`) |
| `BASEDNEWS_SITE_BUCKET` | `s3://<site-bucket>/data` | where the export's JSON goes |
| `BASEDNEWS_PUBLISH_POSITIONS` | unset, or `1` | A54: the maintainer decides whether the latest fit is public |
| `BASEDNEWS_RUN_BUDGET_USD` | e.g. `1` (weekly job only) | a paid step refuses to run without it (A48) |
| `OPENAI_API_KEY` | from SSM `/basednews/openai_api_key` | never in the image or the task definition's plain text |
| `BASEDNEWS_COMMIT` | baked into the image at build | every record names the code version |

The command is `<step> --run-id <execution name>`; exit codes: 0 ok · 1 step failed · 2 gate failed · 3 refused
(configuration) · 4 the stored database changed meanwhile.

## When something fails
1. Step Functions console → the failed execution shows which step failed, with a link to its logs.
2. The step's record (`runs/<run_id>/<step>.json` in the private bucket, also for failed steps) has the commit, the
   counts before and after, the gate's numbers and the error with its traceback. Locally:
   `uv run python -m pipeline.job history`.
3. Fix, push (CI deploys the new image), and re-run the execution.
