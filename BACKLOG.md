# BACKLOG

Working agreement (same as pf): **only items marked `executable: yes` get built.** Writing an item down makes it
visible, not authorised. Capture new wants/bugs here before a session ends.

## P0 — Foundations
- [x] PLAN.md, ASSUMPTIONS.md, VERIFY.md, CLAUDE.md — executable: yes (2026-10-10)

## P1 — Rating core (the USP), offline
- [x] `pipeline/` package skeleton + contracts — executable: yes (2026-10-10)
- [x] M8 Bayesian Bradley–Terry with position-bias β, Laplace posterior — executable: yes (2026-10-10)
- [x] M6 planner: expected-information-gain pair selection + stop rule + budget — executable: yes (2026-10-10)
- [x] Simulator + eval: active vs random, calibration, recovery — executable: yes (2026-10-10)

### P1 findings → follow-ups (captured 2026-10-10)
- [x] Measure real candidate pool: ≈330 comparable outlet pairs/day (≈270 cross-country) on first data → pool exceeds the LLM budget, so active selection has room
- [ ] Country sd 0.15 not reached within 3,000 calls → revisit A09 target vs LLM cost once M7 gives $/call — executable: no
- [x] Planner across both axes jointly (`plan_joint`, A31)

## Reference set (2026-10-11)
- [x] DNR 2026 two-tier registry: 50 reference (5 × 10) + 23 extra; 20 new outlets health-checked OK (A29) — executable: yes
- [ ] M9: origin + country positions from reference tier only (`reference_groups`) when real positions exist — executable: no
- [ ] Frontend: show tier (e.g. solid = reference, outlined = extra) on the country plane — executable: no
- [ ] France reference tier mostly headline+summary (TF1 Info, franceinfo TDM; Le Monde paywall) → watch A21 in M7 eval — executable: no
- [ ] Backfill history for the 20 new reference outlets (backfill picks them up automatically on its next pass) — executable: no

## Plane with ovals (2026-10-11)
- [x] M9 aggregate: outlet + country plane points, ovals = sd × k per axis, origin = average outlet (A25, A26) — executable: yes
- [x] Preview/eval on simulated 2-axis world: oval coverage 45 % outlets / 50 % countries vs 39 % expected — executable: yes
- [x] Angular `app-bias-plane` component + plane page (world → click country → outlets), now the home page — executable: yes
- [x] `scripts/start.sh` / `stop.sh` (pf-style); pf stack stopped, BasedNews served on :8080/:4200 — executable: yes
- [x] (resolved 2026-10-11 by removing the Spring Boot backend and its pages, ADR-010) Backend read the old v1 registry; Ingestion/Dev pages broken
- [ ] (now: static JSON from the export step, ADR-010) Replace `assets/plane-demo.json` with real plane points — executable: no
- [ ] Choose oval multiplier k for the public site (A25) — needs the maintainer
- [ ] Choropleth world map (countries coloured by position) alongside the plane — executable: no

- [ ] Site copy: "relative to the average of N outlets in M countries"; axis words as directions, never verdicts — executable: no

## Trends over time (2026-10-10) — the open rating/aggregation items moved to the MUST section at the end
- [x] M2b backfill: sparse synchronised 10-year Wayback sample, resumable, outage-tolerant (A23, A24) — executable: yes
- [x] `scripts/overnight.sh`: live ingest every 2 h + backfill, caffeinated — first run 2026-10-10 21:44 — executable: yes
- [ ] Filter non-news pages (contact/promo) in backfill at source — executable: no
- [x] Bug fixed 2026-10-11: one failing archived ARTICLE was treated as a global archive.org outage → stalled from ~03:00. Now skipped per article.
- [ ] If archive.org stays restricted: GDELT / Common Crawl News fallback for history — executable: no
- [x] Timestamp every judgment (`event_time`, `judged_at`) in the contract — done before any real judgment exists — executable: yes
- [ ] No-LLM trends from M5: coverage volume, topic share, blindspots per week — executable: no

## P2 — Data modules hardening (M1–M5)
- [x] Refactor `scripts/ingest_feeds.py` into M1 sources + M2 ingest with eval — executable: yes (2026-10-10)
- [x] robots.txt / TDM opt-out check (in M1, applied by M2) (A10) — executable: yes (2026-10-10)
- [x] M4 embed + M5 events (two methods) + eval + labelling tool — executable: yes (2026-10-10)
- [x] (done 2026-10-11 by Claude: 200 labelled pairs, threshold 0.60 kept — VERIFY 2.3) Label pairs → pick M5 threshold
- [ ] Run ingest on a schedule (2–4×/day) so per-day pool stats and freshness become real — executable: no
- [ ] Headless-browser or alternative text for 0 %-text outlets (NYT, WaPo, Daily Mail, Sky, Le Monde, Libération), only where robots/TDM allow — executable: no
- [ ] Topic filter: drop sport/entertainment before judging (politically uninformative, wastes budget) — executable: no
- [x] Extend outlet registry to the 10 MVP countries (A15) — 53 outlets, 52 usable — executable: yes (2026-10-10)
- [ ] Le Parisien feed has titles only (no dates/summary) + TDM-reserved → replace with another French outlet — executable: no
- [ ] Find a working national Corriere della Sera feed — executable: no
- [x] (done 2026-10-11) Retired `scripts/ingest_feeds.py` + `scheduler.py` (pre-pipeline prototype; in git history)

## P3 — Judge
- [x] (built 2026-10-11, A46) M7 judge with structured output, order randomisation, cost log — OpenAI model (A11, A45); first real run needs the key
- [x] E3 semi-real replay + joint two-axis planner + animated plane (GIF + site player) — executable: yes (2026-10-11)
- [x] (built 2026-10-11, `pipeline.judge.eval`) Judge eval set + reliability report — incl. framing-symmetry test ("more right?" vs "more left?", A28) and a
  length-bias test (do verdicts lean with log length ratio? if so, a γ term in M8 — never cut the text, A41) — executable: no

## P4–P6 — see PLAN.md §5
- [x] Country list decided: A15
- [x] Budget decided: A16 (~$25/month). Name kept (A14).
- [x] AWS account set up 2026-10-12 (root MFA, Identity Center admin, $10 budget). Domain deferred.
- [x] Bug fixed 2026-10-11: backfill hung >1.5 h inside one slow download → 90 s wall-clock deadline per request
- [x] Bug fixed 2026-10-11: backfill held the SQLite write lock across slow downloads → live ingest of 20 new outlets crashed ('database is locked'); France had 1 of 5 reference outlets. Now commits per article.
- [x] (fixed 2026-10-11, A39) Bug found 2026-10-11 (M1 eval): bild newest item is −1.9 h old (future) → feed likely gives local time without offset and we read it as UTC; shifts Bild ~2 h in M5's 72 h window — executable: yes
- [ ] M2 contract: record *why* an article has no full text (`text_note`: tdm_reserved | http_403 | bot_challenge | too_short) so the report can split policy from extraction bugs (A33) — executable: yes
- [x] (fixed 2026-10-11, A37 + A39) Bug found 2026-10-11 (M2 report): Dagbladet 85 % dated: 72 legacy rows store "22 Dec, 15:54" (no year → infer from ingested_at), 52 live rows have no pubDate — executable: yes
- [x] (accepted 2026-10-11: headline_only, A38; extra tier) Le Parisien (extra tier): feed has no dates and article pages fail → 0 % usable; fix feed URL or drop — executable: yes
- [x] Bug fixed 2026-10-11 (M2 chart): live-blog entries stored the whole blog's text (569 articles, mostly legacy Nordic) → pipeline.ingest.clean (A34)
- [x] (done 2026-10-11, A38) Boilerplate filter: texts that are cookie walls / consent pages (ANSA "Se hai scelto di non accettare i cookie…") → treat as no text; report column "boilerplate suspect" is the eval — executable: yes
  - Evidence (verification 2026-10-11): 5 of the 15 lowest headline↔text similarities were walls (BFM TV/ANSA/FAZ consent,
    expired Spiegel gift link, Nettavisen comment blurb); 0 of 40 random. Rule: similarity < ~0.15 AND a wall phrase → no text.
- [x] (done 2026-10-11, A38 headline_only) Non-article feed items: VG's feed mixes in VGTV video clips (title only, no text) → live text 43 %; detect og:type video / '- VGTV' and store as kind=video, excluded from text checks — executable: yes
- [x] **A34 v2 — headline history (2026-10-11): arbitrary renames, history respected in M4 + M7** — done 2026-10-11 (M3 normalize, A35, A36)
  1. M2: a known URL whose feed headline changed → new row in `headlines(article_id, title, first_seen, url, how)` (no download).
  2. M3 normalize (new module, ml group): same text under different headlines → decide by content, not count:
     headline↔text similarity (MiniLM; measured: live blogs have one clear match 0.57–0.80, rest ≲ 0.4) + **summary
     similarity** (renamed article keeps its summary; live-blog entries each have their own — to verify) → matches merge
     into one canonical article (`articles.canonical_id`), non-matches lose the page text (backup table). Prefer false
     splits over false merges (car-crash entries score 0.47–0.57 against another crash's text).
  3. M4: vector = L2-normalised mean of embed(headline_k + body) over versions (MiniLM truncates at 128 tokens, so no
     concatenation); store an input hash so new versions trigger a re-embed. M5 excludes aliases.
  4. M7 contract: `pipeline/judge/render.py` shows latest headline + earlier versions with timestamps (cap ~6); E3 cost
     uses the rendered length.
  5. Eval: similarity histogram, live-blog URL groups split correctly (≤ 1 match), sample viewer; tests for rule/render/mean.
  - Also seen: Dagbladet "articles" that are a front-page list ("Annonse Annonse Siste Nyheter…") → container too.
- [x] (fixed 2026-10-11, net.iri_to_uri) Backfill: URLs with non-ASCII characters crash urllib (UnicodeEncodeError, tgcom24 2017) → percent-encode the path in net.fetch — executable: yes
- [x] (done 2026-10-11, A38: policy pages + section hubs → kind=page) Backfill picks section/landing pages as articles (found in 1 · Collect sign-off, sample C3: Bild 'FC Augsburg: Aktuelle News…', URL 'startseite-…'); filter by og:type=article / JSON-LD NewsArticle, merge with the non-article-items item — executable: yes

## Future (2026-10-11)
- [ ] **Judge prompt optimization (3.2 / M7)** — evolutionary search: 10 prompts → score → keep top 5 → LLM writes 5 more
  "in the spirit of" the winners → repeat (OPRO / EvoPrompt-style). Needs the judge + API key — executable: no
  - Objective is NOT decisiveness alone (Goodhart: "never say tie" scores 100 %, and decisive-for-a-systematic-reason
    prompts look consistent but are wrong). Score = decisions that survive an A/B order swap, minus decisions on null
    pairs (same wire story in two outlets, article vs itself), plus detection of planted differences (article vs a
    rewrite that leans clearly further), plus "more left?"/"more right?" mirror agreement (A28); per token.
  - Data hygiene: select on a dev set, report the winner on a fresh test set (winner's curse), freeze one prompt version
    for production and store `prompt_version` on every judgment; never select by agreement with expected positions.
  - Cost: ~1.4 M input tokens per round (10 prompts × 100 pairs × 2 orders); cut losers early (successive halving).
  - Becomes hypothesis H6 in the README.
- [ ] **Deployable app: Docker locally, containers on AWS** — executable: no
  - docker compose: Postgres + pgvector, pipeline worker (scheduled runs), Spring Boot API, Angular (SSR) site, Caddy (HTTPS).
  - store.py → Postgres (A18; the largest piece), migrations, `.env` untracked.
  - AWS (superseded by ADR-010): one EC2/Lightsail instance running the same compose file (ECS + RDS + ALB would
    exceed the ~$25/month budget, A16); images built by GitHub Actions; DB backups to S3; Terraform for the instance.
  - Measured: /metrics + uptime alerts + per-module run log (the `runs` table) + LLM spend cap; updated: CI deploy on tag.
- [x] (done 2026-10-11, A38; also 974 summaries with raw HTML) JSON-LD text keeps HTML entities (`&nbsp;`, `&quot;`, `&#8220;`) → html.unescape in extract (20minutos, Sky News, il Fatto) — executable: yes
- [x] (done 2026-10-11, A38: kind=rundown) 2.3: multi-topic live/broadcast pages (TF1 "LCI Live du samedi…", "20H … du samedi") get clustered with single stories → mark as containers before clustering — executable: yes
- [x] (done 2026-10-11, measure.run candidates) 3.1: pair like with like — news with news, opinion with opinion (A43)
- [ ] M8: tie-aware Bradley–Terry (Davidson) so same-text pairs and judge ties count as "equal" (A44, A06) — executable: no
- [x] (code done 2026-10-11, A47: backend + profiles + `pipeline.embed.calibrate`; run needs the key) **2.2 → OpenAI embeddings** (A45): add an `openai` backend behind 2.2's contract (model name is already part of
  `embeddings`), embed the current window, compare with MiniLM on the 200 labelled pairs (P/R/F1, language gap),
  recalibrate the 2.1 and 2.3 thresholds, write an ADR (MiniLM baseline vs OpenAI). Needs OPENAI_API_KEY — executable: no
- [x] (done 2026-10-11, ADR-009) M8: hierarchical Bradley–Terry with article effects (A49) + simulator test of sd coverage (E5)
- [ ] Judge eval: add a quoted-speech check (pairs whose headline quotes a politician: is the quote attributed to the
  outlet?) — E4c found this error — executable: yes
- [ ] Hierarchical fit at scale: sparse Newton/Cholesky when articles reach thousands (dense now) — executable: yes
- [ ] σ_a underestimated with few repeats (Laplace, E5): try a half-normal prior on σ_a or a check against MCMC — executable: yes

## Finishing — the method is done, the surroundings are not (2026-10-11)
README "What's left" summarises these and the open items above.
- [ ] One refresh command (sources → ingest → normalize → embed → stories) + a weekly measure run with a fixed budget,
  on a schedule; replaces `scripts/overnight.sh` and `scripts/scheduler.py` — executable: no
- [ ] Export the current fit (`positions`, hierarchical, current prompt) to plane JSON / an API endpoint; replace the E4
  replay (prompt v3, flat model) and `plane-demo.json` on the site — executable: no
- [ ] Repeat the pre-registered US/UK check (E6 Q2, H7) on next week's fresh stories, ≈ $1 — needs the maintainer's go
- [ ] Finish the history backfill (2021–2025 not fetched; resumable) — executable: no

### MUST (2026-10-11): historical change graphs — positions over time (A22)
Not optional: the product needs each outlet's and country's position per period over the last 10 years, with
uncertainty, drawn as change graphs. Today every verdict is pooled into one fit. Steps:
- [ ] Data: finish the backfill (above), then measure same-story pairs per period in the archive sample (≈ 4 articles
  per outlet per date may give thin overlap → decide the period length from this) — executable: no
- [ ] M8 v1: per-window fits (rolling 30 days or monthly), same hierarchical model per window — executable: no
- [ ] M8 v2: dynamic Bradley–Terry (random walk, drift q as config) + simulator with planted drift: detects real moves,
  no false moves from outlet-set changes; calls per period to track at a given sd — executable: no
- [ ] M6: budget split "converge today" vs "track the past" — executable: no
- [ ] M9: trend aggregates at time t over the outlets active at t; optional drift-vs-composition split — executable: no
- [ ] Site: change graphs per outlet and per country with uncertainty bands — executable: no
- [ ] Spend: judging the archive sample is paid → budget estimate + the maintainer's go

### Logging (2026-10-11: "recording everything of value from the calls")
- [x] (done 2026-10-11, A51) Full archive per judge call: exact request, full response, timing/retries/failures,
  planner context; failed calls no longer abort a batch — `pipeline/judge/calllog.py`, `tests/test_calllog.py`
- [ ] Check at the next paid run: `pipeline.judge.calllog` → 100 % of paid calls logged, hashes match — needs the maintainer's go (spend)
- [ ] Test whether gpt-5.4-mini returns token logprobs with reasoning on (one call, ≈ $0.002); if yes, request and store
  them (a per-verdict confidence) — needs the maintainer's go
- [ ] Same treatment for embedding calls: log model snapshot + request hash per batch (vectors are already stored) — executable: no

## Infrastructure (2026-10-11: "an actual task of value") — ADR-010, A52
In build order. The maintainer marked the job runner, retention, export, Docker and SQLite-in-S3 executable on 2026-10-12;
the rest waits.
- [x] (decided 2026-10-12, A56) Embeddings via the OpenAI API, 512 dimensions, vector cache for normalize, monthly cap enforced; image without torch
- [x] (E7, 2026-10-12, $0.045) Calibrated `openai:text-embedding-3-small` at 512 and 1536: 512 = 1536; vs MiniLM undecided (biased label set) — docs/experiments/e7_results.md
- [x] (E7b, 2026-10-12, free) Unbiased pooled test → OpenAI @512 adopted as the default, story threshold 0.56 — docs/experiments/e7b_results.md
- [x] (done 2026-10-12, A53) Job runner `python -m pipeline.job <step|job>`: a record per step in `job_runs` + JSON (commit, args, counts before/after, gate numbers, error and traceback), JSON-line logs, eval gates, exit codes 0–4, paid steps need a budget — `pipeline/job/`, `tests/test_job.py`
- [x] (done 2026-10-12, A52) Retention with a dry run by default: Parquet lake (monthly, read back and hash-checked) before any delete; old text and embeddings, story runs but the latest 2, archived call-log blobs; VACUUM; refit check — `pipeline/retention/`, `tests/test_retention.py`
- [x] (done 2026-10-12, A54) Export: the current fit → `plane-latest.json` (2.6 kB gzip) with a payload check; site "Latest fit" view; animation files rounded (1.3 MB → 67 kB gzip). Trends join it with the trend model — `pipeline/export/`
- [x] (done 2026-10-12) Dockerfile (one image, optional CPU-only MiniLM via WITH_ML=1, commit baked in, non-root), allow-list .dockerignore, docker compose against a local stand-in bucket (`data/state`). Built and run: export, stories, embed and history through compose; slim image ≈ 0.7 GB unpacked (≈ 430 MB compressed), ML image ≈ 2.2 GB (torch CPU + MiniLM 0.46 GB)
- [ ] Smaller image: matplotlib is only for reports and could leave the job image — executable: no
- [x] (done 2026-10-12, A55) SQLite-in-S3: download → run → upload only on success, conditional on the downloaded version (If-Match), WAL folded + quick_check, records always uploaded, lake before database, `push-db` for the first upload — `pipeline/job/storage.py`, `tests/test_storage.py` (fake bucket, fake S3 client)
- [x] (2026-10-12) CI builds the slim image on every push and imports every job module in it (the check for the Dockerfile)
- [ ] Decide arm64 (Fargate Graviton, ≈ 20 % cheaper) vs amd64 for the task — with the Terraform — executable: no
- [ ] Site deploy must not delete `data/` in the site bucket (the export's files live there) — part of the CI deploy
- [ ] Terraform (state in S3 with locking): S3 (site + private), CloudFront (default address; domain later), ECR, ECS Fargate, Step Functions, EventBridge, SSM, CloudWatch, Budgets — executable: no
- [ ] GitHub Actions: tests → image → ECR → site deploy → infrastructure apply, with OIDC — executable: no
- [ ] Alarms: failed execution, spend → email — executable: no
- [ ] History lake: monthly Parquet export (verdicts, positions, runs, call-log archive) to S3 + Athena table definitions; DuckDB query example in docs — executable: no
- [ ] OpenTelemetry in the job runner → Grafana Cloud free tier (traces per job and step, pipeline metrics); token in SSM — executable: no
- [ ] CI: `terraform plan` on pull requests, tflint + checkov — executable: no
- [ ] Later: choose and buy a domain (Route 53), then add the certificate (us-east-1) and alias — needs the maintainer
- [x] Decided 2026-10-11: drop the Spring Boot API, Terraform, eu-north-1 (Stockholm)
- [x] (done 2026-10-11) Removed `backend/` and the Ingestion/Dev pages; `scripts/start.sh` starts the site only; CI workflow (tests, lint, site build)

