# Deploy runbook

How BasedNews goes from a laptop to a public HTTPS site on AWS. Design and costs: [ADR-010](decisions/ADR-010-deployment.md).
Principle: free until free doesn't work. Expected cost ≈ $4–7/month plus AI spend (capped).

---

## Step 1 — accounts and tools (the maintainer, ≈ 1–1.5 h, no code needed)

**1.1 OpenAI**
- [ ] Settings → Limits: set a **monthly hard limit** (e.g. $12) — the provider-side cap behind A48.
- [ ] Create a separate project + key for production (`basednews-prod`), so the local and production keys can be
      revoked independently. Do not paste it anywhere yet (step 3.2).

**1.2 AWS account** (personal email and card)
- [ ] Sign up at aws.amazon.com. New accounts get starter credits — check the current free-tier terms when signing up.
- [ ] Root user: **turn on MFA right away**, then stop using root.
- [ ] IAM Identity Center (region **eu-north-1**): enable it, create your user, give it the `AdministratorAccess`
      permission set on this account. All CLI work uses this user, never root.
- [ ] Billing: turn on Cost Explorer and create an **AWS Budget**: monthly cost budget of $10, email at 80 % actual and
      100 % forecast.

**1.3 Tools on the Mac**
- [ ] `brew install awscli` and `brew tap hashicorp/tap && brew install hashicorp/tap/terraform`
- [ ] Docker Desktop (free for personal use) or OrbStack, to build and run the job image locally.
- [ ] `aws configure sso` → profile name `basednews`, region `eu-north-1`; check with
      `aws sts get-caller-identity --profile basednews`.

**1.4 Domain**
- [ ] Pick a name and register it in Route 53 (Registered domains → Register). Price depends on the ending (.com is
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

| # | Piece | Check |
|---|---|---|
| 2.1 | Job runner `pipeline.job <stage>`: a `runs` row per step (commit, counts, eval numbers, error), structured logs, the module eval as a gate | unit tests; a failing eval stops the run |
| 2.2 | Export step: positions → JSON for the site (< 100 kB per view); slim the animation files | payload size check; site renders from the export |
| 2.3 | Retention (A52) + monthly Parquet history | dry-run report; refit from kept tables reproduces positions |
| 2.4 | Dockerfile + docker compose (the same image runs locally) | `docker compose run job ingest` works end to end |
| 2.5 | SQLite-in-S3 wrapper: download → run → upload on success, single writer | local test against a fake bucket |
| 2.6 | Terraform `infra/bootstrap` (state bucket, GitHub OIDC role) and `infra/main` (S3 + CloudFront + certificate, Route 53, ECR, ECS + task definitions, Step Functions, EventBridge schedules, SSM, CloudWatch alarms, Budgets) | `terraform plan` is clean; checkov/tflint in CI |
| 2.7 | CI deploy: image → ECR, site → S3, `terraform plan` on pull requests | the workflow runs green |
| 2.8 | OpenTelemetry from the job runner to Grafana | one trace per run, one span per step |

---

## Step 3 — first deploy (together)

1. `terraform -chdir=infra/bootstrap apply` → state bucket and the role GitHub Actions uses (no stored AWS keys).
2. **Secrets, typed by the maintainer in the AWS console** (Systems Manager → Parameter Store → SecureString), not in
   a shell, so they never land in shell history: `/basednews/openai_api_key`, `/basednews/grafana_otlp_token`.
3. `terraform -chdir=infra/main plan`, read it, then `apply`.
4. Upload the current hot database to the private bucket (one time).
5. Push to `main` → CI builds the image into ECR and the site into S3.
6. Start one **refresh-only** run by hand in Step Functions (no paid steps). Read the `runs` rows and the Grafana
   trace.
7. Open the domain over HTTPS.
8. Turn on the schedules: hourly refresh; weekly measure with a budget — **the measure schedule needs the maintainer's
   go**, because it spends money.

## When something fails
1. Step Functions console → the failed execution shows which step failed, with a link to its logs.
2. The step's `runs` row has the commit, the counts in and out, the eval numbers and the error with its traceback.
3. Fix, push (CI deploys the new image), and re-run the execution.
