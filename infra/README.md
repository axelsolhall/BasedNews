# infra/

Terraform for production (ADR-010). How to apply, in order: [docs/DEPLOY.md](../docs/DEPLOY.md), step 3.

| Path | What |
|---|---|
| `bootstrap/` | the S3 bucket for Terraform's state (local state, applied once) |
| `main/storage.tf` | private bucket (database, step records, lake, lock) and site bucket |
| `main/site.tf` | CloudFront on its own `*.cloudfront.net` address |
| `main/jobs.tf` | ECR, ECS cluster, task definition (arm64), roles, security group, log group |
| `main/pipeline.tf` | the four jobs as Step Functions state machines, and their schedules (created off) |
| `main/alerts.tf` | failed-run alarms → email; the kill-switch budget and Lambda (`killswitch/handler.py`) |
| `main/github.tf` | OIDC provider and the two CI roles, deploy and plan (A57) |
| `.checkov.yaml` | security scan settings; every skipped check has its reason |

Checks: `terraform fmt -check -recursive infra`, `terraform -chdir=infra/main validate`,
`uvx checkov -d infra --config-file infra/.checkov.yaml`, `uv run python -m pytest -q tests/test_killswitch.py`.
