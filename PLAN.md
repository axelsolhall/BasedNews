# BasedNews — Master Plan

> Standing rules: `~/.claude/CLAUDE.md` (pipeline modules · every module evaluable · assumptions recorded)
> plus this repo's `CLAUDE.md`. Open questions & assumptions: `ASSUMPTIONS.md`. Sign-offs: `VERIFY.md`.
> Work queue: `BACKLOG.md` (only items marked `executable: yes` get built).

## 1. Product

**Promise to the user:** *See how your country's news — and the world's — leans, how sure we are about it,
and what the other side is covering that your outlets aren't.*

### Value the user gets (in priority order)
1. **World map — "how does each country's national media lean?"** Countries coloured by the audience-weighted
   position of their major outlets on two axes (economic left–right, liberal–conservative), with uncertainty
   shown, not hidden. Click a country to drill down.
2. **Country page — "my news landscape".** Every major outlet on a 2-D plane with uncertainty ellipses;
   today's top stories with a *coverage spectrum* (which side covered it, how much); **blindspots** — stories
   covered almost only by one side.
3. **Event lens — "countries vs countries".** For an international event, how each country's media framed it,
   side by side.
4. **Outlet page.** Position over time, confidence, sample comparisons: the two headlines (linked, never the text,
   A10) and the verdict (transparency).
5. **Trends over time.** How an outlet's and a country's position moves month to month (with uncertainty bands),
   plus no-LLM-cost trends: coverage volume, topic share, blindspots over time. (2026-10-10)
6. **Methodology page.** Prompts, model, maths, evaluation results, known limitations. Trust is the product.

Non-goals (for now): accounts, comments, personalised feeds, paid tiers.

### Core principle: everything is relative (2026-10-11)
Nobody agrees where "the centre" is or how biased any outlet "really" is, so BasedNews never claims absolute positions.
Every number is **relative**: outlets relative to each other, countries relative to each other. The origin (0, 0) is simply
the average outlet in our sample, a reference point, not a political centre. The axis words (left/right,
liberal/conservative) are **directions**, not verdicts. UI and methodology copy must say this plainly.

### What makes it different (USP)
LLM judgments cost money, so **every comparison is chosen to maximally reduce uncertainty**. We report a
posterior standard deviation for every outlet and country and stop spending once it is below target.
Headline metric: **LLM calls needed to reach target certainty, active selection vs random.**

## 2. Pipeline (Python, `pipeline/`)

Each module: one job · typed input/output · persisted output · own `eval` · replaceable.
Contracts are tables in the hot SQLite database plus monthly Parquet files in the history lake (ADR-010, A52); types
in `pipeline/contracts.py`.

| # | Module | In → Out | Eval (how a human checks it) |
|---|---|---|---|
| M1 | **sources** | outlet registry (JSON) → validated outlets + live feeds | feed health report: reachable, items/day, last item age |
| M2 | **ingest** | feeds → `articles` (metadata, text kept internally) | per-outlet counts/day, extraction success %, duplicate %, sample viewer |
| M3 | **normalize** | articles → canonical articles + headline history (merge re-headlined copies, strip live-blog/news-list page texts, A34) | free-label checks: live-blog URLs → container, identical summaries → renamed; similarity histograms; sample viewer |
| M4 | **embed** | clean text → vectors (multilingual) | nearest-neighbour sanity; cross-lingual pair recall on labelled translations |
| M5 | **events** | vectors + time → `events` (article clusters, within & across countries) | labelled same-event pairs → precision / recall / F1; cluster viewer |
| M6 | **planner** | events + current posterior + budget → `comparison_requests` | simulator: posterior sd & rank accuracy vs #calls, active vs random |
| M7 | **judge** | comparison requests → `judgments` (forced-choice verdict per axis, A46) + full call log (A51) | fixed eval set: order-swap consistency, test–retest, cross-model agreement, $/call |
| M8 | **rate** | judgments → `positions` (mean, sd per axis; hierarchical, outlets over articles, ADR-009) | simulator recovery; credible-interval coverage; correlation with declared affiliations |
| M9 | **aggregate** | positions + coverage → country positions, story spectra, blindspots | invariants (weights sum to 1, sd shrinks with data); spot-check viewer |
| M10 | **publish** | aggregates → static JSON for the site + Parquet history | schema check; payload under ~100 kB per view |

Orchestration: `python -m pipeline.<module>.run` (and `.eval`), each module idempotent and runnable alone; in
production one Step Functions step per module (ADR-010).
M6→M7→M8 form the **active-learning loop** (plan → judge → update → plan…), bounded by a daily LLM budget.

## 3. Serving (ADR-010)
- **No API server.** The export step writes small JSON files next to the site; the browser only draws them.
- **Frontend:** Angular (`frontend/`), built to static files; prerendering for SEO later (country and outlet pages
  must be crawlable). Map: SVG/TopoJSON choropleth.
- **Data store:** a small hot SQLite database kept in S3 (bounded window, A52) and a Parquet history lake in S3.
  Postgres only when parallel jobs or size require it.

## 4. Production on AWS (ADR-010)
- **Shape:** scheduled container jobs (ECS Fargate) orchestrated by Step Functions; static site and data on S3 +
  CloudFront; Terraform; GitHub Actions with OIDC; region eu-north-1. Free until free doesn't work.
- **Security baseline:** no servers and no open ports; database and secrets private (S3, SSM); least-privilege IAM
  per job; security headers (CSP, HSTS) via CloudFront; dependency, container and Terraform scanning in CI; S3
  versioning as backup with a tested restore; LLM spend capped in code and in the provider dashboard.
- **SEO baseline:** prerendered pages, per-page title/description/Open Graph, `sitemap.xml`, `robots.txt`.
- **Ops:** a `runs` row per step, structured logs, CloudWatch alarms by email, OpenTelemetry to Grafana's free tier.
  Runbook: [docs/DEPLOY.md](docs/DEPLOY.md).

## 5. Phases
| Phase | Delivers | Exit criterion |
|---|---|---|
| P0 | Docs, contracts, ASSUMPTIONS register | ✅ the maintainer has read and agreed with PLAN + assumptions |
| P1 | **Rating core (M6+M8) + simulator** — the USP, offline | ✅ calls-to-target-sd, active vs random, on simulated outlets |
| P2 | M1–M5 hardened on 10 countries + eval sets | ✅ stages 1–2 signed off 2026-10-11 |
| P3 | M7 judge + live loop on real data (small budget) | ✅ method complete; validation on fresh data in progress (H7) |
| P3b | **Positions over time (must)** | change graphs per outlet and country with uncertainty bands (A22) |
| P4 | World map; M9 aggregate from the reference tier | Map shows 10 countries with sd under target |
| P5 | Frontend product pages + prerendering/SEO | Country/outlet/event pages working locally |
| P6 | AWS production deploy, security & ops baseline | Public HTTPS URL, monitored, budget-capped (next: docs/DEPLOY.md) |
