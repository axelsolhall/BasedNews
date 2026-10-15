# BACKLOG

Working agreement: **only items marked `executable: yes` get built.** Writing an item down makes it visible, not
authorised. Capture new wants and bugs here before a session ends. Paid runs always need the maintainer's go, even when
the code exists. Done items are kept, condensed, at the end.

## Now — getting it running (2026-10-12)
- [ ] First deploy: state bucket → OpenAI key in SSM → `terraform apply` → GitHub variables → push → `push-db` → one
  refresh by hand (docs/DEPLOY.md step 3) — needs the maintainer
- [ ] Switch on `refresh_enabled` after the first refresh looks right — needs the maintainer
- [ ] Switch on `judge_enabled` (≤ $0.15 a day) — needs the maintainer's go
- [ ] A54: publish the latest fit on the deployed site, labelled "not validated", or keep it private — needs the maintainer
- [ ] Repeat the pre-registered US/UK check (E6 Q2, H7) on stories collected after the method was frozen, ≈ $1 — needs the maintainer's go
- [ ] After the next paid run: `pipeline.judge.calllog` → 100 % of paid calls logged, hashes match (A51)
- [ ] Does gpt-5.4-mini return token logprobs with reasoning on (one call, ≈ $0.002)? If yes, store them as a
  per-verdict confidence — needs the maintainer's go

## MUST — historical change graphs: positions over time (A22)
Not optional: the product needs each outlet's and country's position per period over the last 10 years, with
uncertainty, drawn as change graphs. Today every verdict is pooled into one fit.
- [ ] Data: finish the backfill (2021–2025 not fetched; resumable), then measure same-story pairs per period in the
  archive sample (≈ 4 articles per outlet per date may give thin overlap → decide the period length from this) — executable: no
- [ ] M8 v1: per-window fits (rolling 30 days or monthly), the same hierarchical model per window — executable: no
- [ ] M8 v2: dynamic Bradley–Terry (random walk, drift q as config) + a simulator with planted drift: detects real
  moves, no false moves from outlet-set changes; calls per period to track at a given sd — executable: no
- [ ] M6: budget split "converge today" vs "track the past" — executable: no
- [ ] M9: trend aggregates at time t over the outlets active at t; optional drift-vs-composition split — executable: no
- [ ] Export + site: trends in the export (4.2); change graphs per outlet and per country with uncertainty bands — executable: no
- [ ] Spend: judging the archive sample is paid → budget estimate + the maintainer's go

## Open — operate and infrastructure
- [x] Self-healing operations (ADR-012, A62): lease lock, clean stop, step retry, image smoke test (no deploy rollback),
  heal-aware alarms — 2026-10-15
- [ ] OpenTelemetry in the job runner → Grafana Cloud free tier (a trace per job, a span per step, pipeline metrics);
  token in SSM (docs/DEPLOY.md 2.8) — executable: no
- [ ] The lake, queryable: Athena table definitions over `lake/` + a DuckDB query example in the docs (the Parquet files
  themselves are written by retention since 2026-10-12) — executable: no
- [ ] After a month of production: review the gate thresholds against the values in the step records (A53), the
  retention window (A52) and the real bill vs the estimate — needs the maintainer
- [ ] Smaller image: matplotlib is only for reports and could leave the job image — executable: no
- [ ] Embedding calls: log model snapshot + request hash per batch, like the judge's call log (A51) — executable: no
- [ ] A35 with OpenAI: one vector from all headline versions concatenated vs the mean of versions; measure on E7b's
  pooled set (free) — executable: no
- [ ] Ingest report: after the first retention `--apply`, old live articles show no text (pruned); split "pruned" from
  "never had text" in the report's text share — executable: no
- [ ] tflint in CI (checkov is there) — executable: no
- [ ] Security baseline gaps (PLAN §4): a Content-Security-Policy header on CloudFront; dependency scanning in CI
  (pip-audit / npm audit); a tested database restore from an S3 version after the first deploy — executable: no
- [ ] A domain (Route 53), then the certificate (us-east-1) and the CloudFront alias — needs the maintainer
- [ ] The backup and legacy tables (`articles_legacy`, `articles_text_backup`, `articles_shared_text`, ≈ 30 MB):
  keep, move to the lake, or drop — needs the maintainer

## Open — data (1 · Collect, 2 · Clean)
- [ ] M2 contract: record *why* an article has no full text (`text_note`: tdm_reserved | http_403 | bot_challenge |
  too_short), so the report can split policy from extraction bugs (A33) — executable: yes
- [ ] Filter non-news pages (contact, promo) in backfill at the source — executable: no
- [ ] If archive.org stays restricted: GDELT / Common Crawl News as a history fallback — executable: no
- [ ] Backfill history for the 20 outlets added to the reference set (the backfill picks them up on its next pass) — executable: no
- [ ] Headless browser or another text source for 0 %-text outlets (NYT, WaPo, Daily Mail, Sky, Le Monde,
  Libération), only where robots/TDM allow — executable: no
- [ ] Topic filter: drop sport/entertainment before judging (politically uninformative, wastes budget) — executable: no
- [ ] France's reference tier is mostly headline + summary (TF1 Info, franceinfo TDM; Le Monde paywall) → watch A21 in
  the judge eval — executable: no
- [ ] Le Parisien (extra tier): titles only, no dates, TDM-reserved → replace with another French outlet — executable: no
- [ ] A working national Corriere della Sera feed — executable: no
- [ ] No-LLM trends from 2.3: coverage volume, topic share, blindspots per period — executable: no

## Open — method (3 · Measure)
- [ ] Judge eval: a quoted-speech check (pairs whose headline quotes a politician: is the quote attributed to the
  outlet?) — E4c found this error — executable: yes (running it is paid)
- [ ] Hierarchical fit at scale: sparse Newton/Cholesky when articles reach thousands (dense now) — executable: yes
- [ ] σ_a underestimated with few repeats (Laplace, E5): a half-normal prior on σ_a, or a check against MCMC — executable: yes
- [ ] Tie-aware Bradley–Terry (Davidson) so same-text pairs and judge ties count as "equal" (A44, A06) — executable: no
- [ ] Economic axis: most stories have no economic angle → filter by topic before planning — executable: no
- [ ] Country sd 0.15 not reached within 3,000 simulated calls → revisit the A09 target against real $/call — executable: no
- [ ] **Judge prompt optimisation (H6)** — evolutionary search (OPRO / EvoPrompt-style): 10 prompts → score → keep the
  top 5 → an LLM writes 5 more in their spirit → repeat. Needs the API — executable: no
  - The objective is not decisiveness alone (Goodhart: "never say tie" scores 100 %). Score = decisions that survive
    an A/B order swap, minus decisions on null pairs (same wire story, article vs itself), plus detection of planted
    differences (an article vs a rewrite that leans clearly further), plus "more left?"/"more right?" mirror agreement
    (A28); per token.
  - Hygiene: select on a dev set, report the winner on a fresh test set (winner's curse), freeze one prompt version
    for production; never select by agreement with expected positions.
  - Cost: ≈ 1.4 M input tokens per round (10 prompts × 100 pairs × 2 orders); cut losers early (successive halving).

## Open — coverage: Europe and North America (proposal: docs/decisions/proposal-more-countries.md)
- [ ] Decide scope, order, judge budget and the reference-tier rule outside DNR — needs the maintainer
- [ ] Story clustering that scales past ≈15k articles per window (≈34k at 41 countries needs ≈4.6 GB today) — executable: no
- [ ] Phase 1: DNR-covered countries — sources, feeds, robots/TDM checks, a small judge eval per new language (paid) — executable: no
- [ ] Phase 2: countries outside DNR, once A29 has a rule for them — executable: no

## Open — site (4 · Serve)
- [ ] Choose the oval multiplier k for the public site (A25) — needs the maintainer
- [ ] Country positions and the origin from the reference tier only (`reference_groups`) once real positions are public — executable: no
- [ ] Show the tier on the country plane (solid = reference, outlined = extra) — executable: no
- [ ] Replace the demo and the E4 replay with the export once A54 says the latest fit is public — executable: no
- [ ] Site copy: "relative to the average of N outlets in M countries"; axis words as directions, never verdicts — executable: no
- [ ] Choropleth world map (countries coloured by position) alongside the plane — executable: no

---

## Done (condensed)

**2026-10-12 — infrastructure, embeddings, rolling jobs**
- [x] Job runner `python -m pipeline.job <step|job>`: a record per step (commit, args, counts, gate numbers, error and
  traceback), JSON-line logs, eval gates (A53), exit codes 0–5, paid steps need a budget — `pipeline/job/`
- [x] Export: the current fit → `plane-latest.json` (≈ 3 kB gzip) with a payload check; the site's "Latest fit" view;
  animation files rounded (1.3 MB → 67 kB gzip) — `pipeline/export/` (A54)
- [x] Retention, a dry run by default: monthly Parquet lake (read back and hash-checked) before any delete; old text,
  vectors, story runs and archived call-log blobs; VACUUM; a refit check — `pipeline/retention/` (A52)
- [x] SQLite-in-S3: lock, download, run, upload only on success and only over the downloaded version; records always
  uploaded; the lake before the database; `push-db` for the first upload — `pipeline/job/storage.py` (A55)
- [x] Docker: one arm64 image (optional CPU-only MiniLM via `WITH_ML=1`), commit baked in, non-root; allow-list
  `.dockerignore`; docker compose against a local stand-in bucket; CI builds it on every push
- [x] Terraform `infra/`: state bucket; S3, CloudFront, ECR, ECS Fargate arm64 (2 GB), Step Functions ×4, schedules
  (created off), failure alarms → email, a $15 kill switch, GitHub OIDC roles; validated, read-only plan, job
  definitions accepted by AWS's validator, checkov clean with reasoned skips
- [x] CI: tests, lint, image, site, Terraform fmt/validate + checkov; on main the image → ECR and the site → S3
  (never touching `data/`); a read-only plan on pull requests; never `apply` (A57)
- [x] Embeddings via the OpenAI API at 512 dimensions, vector cache for normalize, monthly cap enforced in the encoder
  (A56, ADR-011); E7 ($0.045): 512 = 1536; E7b (free): blind pooled test, OpenAI ahead of MiniLM, story threshold 0.56
- [x] Jobs roll: hourly refresh, daily feeds, daily judge with a daily budget, daily maintenance; new pairs judged as
  they appear (A58)
- [x] Story clustering builds its distances in blocks: identical stories, half the memory (1.35 → 0.66 GB at 7,000 articles)
- [x] AWS account set up (root MFA, Identity Center admin, $10 budget); OpenAI reload limit $10/month; domain deferred

**2026-10-11 — the method, end to end on real data**
- [x] Judge (A46): structured forced choice, order randomisation, cost log; reliability eval incl. mirror and length
  tests; prompt v1 → v4 via E4/E4b; full call log per paid call (A51)
- [x] Hierarchical Bradley–Terry with article effects (A49, ADR-009) + the E5 simulation of interval coverage
- [x] Planner over both axes jointly, article-aware (`plan_joint_hier`); pairs like with like (A21, A43)
- [x] Headline history (A34 v2: renames, containers, mean vectors over versions, judge sees earlier headlines)
- [x] Cleaning: boilerplate (A40), walls and non-articles (A38), site-name blinding (A42), genre (A43), HTML entities
- [x] Two-tier DNR 2026 reference set: 50 reference + 23 extra outlets (A29, ADR-006)
- [x] 200 labelled same-event pairs; threshold 0.60 kept for MiniLM (later superseded by E7b)
- [x] Backfill fixes: per-article outage handling, 90 s deadline per request, commit per article, non-ASCII URLs,
  section pages filtered; Bild local-time dates (A39); Dagbladet dates (A37)
- [x] Spring Boot API and its pages removed (ADR-010); `scripts/start.sh` serves the site only; CI workflow
- [x] Superseded: "Docker + Postgres + Spring Boot on one EC2/Lightsail instance" — replaced by ADR-010's jobs on
  Fargate, SQLite-in-S3 and a static site

**2026-10-10 — foundations**
- [x] PLAN, ASSUMPTIONS, VERIFY, CLAUDE.md; `pipeline/` skeleton and contracts
- [x] Bradley–Terry with judge order bias β and a Laplace posterior; information-gain planner; simulator (active vs
  random, calibration, recovery)
- [x] Sources + ingest refactored from the prototype script, robots.txt and TDM checks (A10); 10 countries (A15)
- [x] Embedding + story clustering (two methods) + eval + labelling tool; real pool ≈ 330 comparable outlet pairs a day
- [x] Plane with ovals (A25, A26), Angular plane component; backfill: sparse synchronised 10-year Wayback sample
  (A23, A24); every judgment timestamped
