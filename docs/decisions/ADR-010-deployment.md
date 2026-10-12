# ADR-010 — Deployment: a job-based pipeline on AWS, a static site, a small hot database

**Status:** accepted 2026-10-11. AWS; a job-based backend with thorough logging; SQLite-in-S3 over RDS; a bounded
data window (A52); the Spring Boot API dropped (read-only aggregates need no server); Terraform; region eu-north-1 (Stockholm).
Supersedes the stack in A13 for deployment.

## Context
BasedNews has to serve the public internet on about $25/month (A16), with up to $12 of that for AI. Nothing in it
needs an always-on server. Data changes when a job runs (ingest hourly, measure weekly), and readers only need
aggregates. When a deploy fails, the logs must say which step failed, on which code version, and why.

## Standard vs explored
| | Common default | Here |
|---|---|---|
| backend | an always-on API server + load balancer | scheduled container jobs; no server between readers and data |
| data for the site | an API queried per request | precomputed JSON files on a CDN, written by an export job |
| database | managed Postgres (RDS, ~$15/month, always on) | SQLite file in S3, downloaded by each job, uploaded on success (~$0) |
| data retention | keep everything | a bounded window: bulky inputs are deleted once processed, verdicts are kept (A52) |
| failure diagnosis | read the server logs | one execution per run with one step per module, a `runs` row per step, eval gates |

## Decision
- **Site:** Angular built to static files on S3 + CloudFront, served on CloudFront's own HTTPS address until a domain is bought.
- **Published data:** JSON written by an export job next to the site. Only aggregates; article text never leaves the
  private side (A10).
- **Compute:** one Docker image (ECR), one command per module, run as ECS Fargate tasks. Tasks exist only while a job
  runs. They run in a public subnet with no inbound access, so no NAT gateway (~$33/month) is needed.
- **Orchestration:** EventBridge Scheduler starts Step Functions:
  - hourly: ingest → normalize → embed → stories;
  - weekly: measure → aggregate → export → CloudFront invalidation.

  Step Functions runs one execution at a time, so the SQLite file has a single writer; the upload is also conditional
  on the version that was downloaded (A55). The backfill takes its turn in the same queue.
- **Storage:**
  - the hot SQLite database in a private S3 bucket, versioned, with old versions expired;
  - backups and run reports in the same private bucket;
  - the call-log archive (A51) in cold storage.
- **Secrets:** the OpenAI key in SSM Parameter Store, handed to the task at start, never stored in the image.
- **Failure → fix:**
  - the image is tagged with its git commit, and every run records it;
  - each step writes a record: commit, step, counts before and after, the gate's numbers, error and traceback
    (`job_runs`, and as JSON in the private bucket even when the step fails; built 2026-10-12, A53);
  - each step's module eval is a gate, so bad data stops the run and the site keeps its last good data;
  - logs are structured JSON in CloudWatch;
  - a failed execution or a spend alarm sends an email; AWS Budgets and the OpenAI hard limit back the spend caps (A48).
- **Region:** eu-north-1 (Stockholm). Exception: the TLS certificate for CloudFront must be issued in us-east-1, an
  AWS requirement.
- **Delivery:**
  - Terraform for every AWS resource; its state in a versioned private S3 bucket with locking;
  - GitHub Actions: tests on every push; on main, build the image, push it to ECR, deploy the site and apply the
    infrastructure;
  - AWS access through OIDC, with no stored keys.
- **Browser payload budget:** each view loads under about 100 kB compressed. The current animation files (0.6–1.3 MB)
  must be slimmed or split per country before launch.

## Decisions 2026-10-11 (round 2)
- **Principle: keep it free until free does not work.** Every paid upgrade has a named trigger:
  | Free now | Upgrade when | To |
  |---|---|---|
  | SQLite file in S3 | jobs must run in parallel, or the hot database passes ~1 GB | RDS Postgres (~$15/month) |
  | Step Functions | never for cost; only if lineage or the UI becomes the bottleneck | Dagster |
  | Grafana Cloud free tier | the free limits are hit | a paid tier, or CloudWatch only |
- **Orchestration:** Step Functions + EventBridge Scheduler (confirmed).
- **History: a data lake in S3.**
  - Monthly Parquet partitions of verdicts, positions, `runs` rows and the call-log archive (A51, A52).
  - Queried with Athena (pay per scan, pennies) or DuckDB locally.
  - The hot database keeps verdicts too while they are small; the lake is the permanent, append-only copy.
- **Domain:** deferred (2026-10-12). Launch on the free `*.cloudfront.net` address. Later: register in Route 53, issue
  the certificate in us-east-1, add the alias; nothing else changes.
- **Environments:** local (docker compose, the same image) and production. No staging; pull requests run
  `terraform plan` and the tests instead.
- **Observability:**
  - CloudWatch for logs and alarms;
  - plus OpenTelemetry from the jobs to Grafana Cloud's free tier, sent straight from the app with no collector:
    traces per job and step, and metrics such as articles in, verdicts, spend and eval numbers;
  - the Grafana token in SSM next to the OpenAI key.
- **Rejected for cost:** EKS (~$75+/month), RDS for now, NAT gateway, load balancer, Dagster's hosted plan, hosted
  Airflow.

## Consequences
- Cost: about $3–5/month for AWS, plus AI spend up to the cap.
- Locally, docker compose runs the same image against a local file, so a job can be reproduced exactly.
- SQLite-in-S3 holds while the hot database stays small (about 0.5 GB with A52) and jobs run one at a time. If either
  breaks, the move to Postgres is contained behind `pipeline/store.py` (A18).
- No live queries: anything interactive must be precomputed. A search-style feature would need an API, which can be
  added later.
