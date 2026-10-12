# VERIFY — human sign-off

Things the maintainer confirms by hand. Each block: what to run, what "good" looks like, tick when satisfied.

## Open now
- [x] OpenAI: prepaid credits, auto-reload capped at $10/month (checked 2026-10-12, docs/DEPLOY.md step 1).
- [ ] Infra build 2.1–2.5 (2026-10-12): see "5 · Operate" at the end — three decisions are open (A54 publishing,
      legacy tables, retention window).
- [ ] Next paid run (needs a go): repeat the pre-registered US/UK check on fresh stories (H7), then
      `uv run python -m pipeline.judge.calllog` → every paid call logged (A51).
- [x] Embeddings (2026-10-12): OpenAI @512 is the default (A56), story threshold 0.56 from the unbiased test E7b
      (docs/experiments/e7b_results.md). E7's MiniLM comparison is withdrawn (biased set).
- [ ] Optional: spot-check 10 of Claude's 270 blind E7b labels (`data/eval/e7b_labels.jsonl`; the pair texts:
      `uv run python -m pipeline.experiments.e7b show --n 10`).
- [ ] The next normalize run fills the headline vector cache once (≈ $0.003) and every refresh then embeds new
      articles (≈ $0.01/day): both count toward the monthly cap. Needs a go for the first run.
- [ ] Delete the experiment's 1536 vectors (44 MB of the database; $0.02 to rebuy)? Needs your OK (deletes data).
- [ ] Optional: spot-check 10 of Claude's same-event labels (2.3).
- [ ] Open from E4: social length lean 0.73; Fox News reading slightly liberal (docs/experiments/e4b_results.md).

The detailed per-module checks for stages 1–2 (P2) were superseded by the stage sign-offs below and removed.

## P1 · Rating core (M6 planner + M8 rating) on the simulator
- [ ] Run `uv run python -m pytest -q` (6 unit tests) and `uv run python -m pipeline.sim.eval_loop` (~20 s).
      Results land in `reports/p1_rating_core.md` and `reports/p1_active_vs_random.png`.
- [ ] **Recovery:** fitted positions correlate with simulated truth (Spearman ≥ 0.9 at end of budget).
- [ ] **Calibration:** ~90 % of 90 % credible intervals contain the true value.
- [ ] **USP plot:** active selection reaches the target sd with clearly fewer calls than random.
- [x] You understand and agree with A04, A05, A09. (all of A01–A29 accepted 2026-10-11)

## 1 · Collect — stage sign-off (1.1 Sources M1 · 1.2 Ingest M2 · 1.3 Backfill M2b)
**DONE 2026-10-11** (human checks delegated to Claude, results below).
`uv run python -m pipeline.signoff --stage collect` → 10 pass · 2 explained warnings · 0 unexplained failures.
Accepted limitations: Le Parisien contributes nothing (blocks + no summaries); the history backfill keeps filling
(358/730 outlet-years) — data, not code; summary-only outlets by policy (A10, A33, FAZ+/DN paywalls).

- [ ] `uv run python -m pipeline.signoff --stage collect` → automatic checks: 0 unexplained failures (warnings must name
      their BACKLOG item / assumption).
- [ ] Human checks A–D printed by the same command (≈ 10 min): coverage chart, 5 live articles vs their live pages,
      3 archived articles vs archive.org, one TDM and one robots.txt spot-check. `--seed 1` draws new samples.
- Found on the first run (2026-10-11): sample C3 was a Bild section page, not an article → BACKLOG.
- [x] **Text matches the article — verified by Claude 2026-10-11 (human checks delegated).**
  Screen: headline↔text similarity for all 4,625 stored texts (median 0.66; 5 % below 0.3).
  Read 40 random texts (4 per country): **40/40 the right article, starting at its beginning** (95 % CI on the error
  rate: 0–9 %); minor boilerplate at the edges in ~8 (BBC "- Published", NYT "Continue reading…", newsletter blurbs),
  `&nbsp;`/`&quot;` left in 2 JSON-LD texts, 2 video pages with a short description.
  Read the 15 lowest-similarity texts: **5 are walls, not articles** (cookie/consent: BFM TV, ANSA, FAZ; expired Spiegel
  gift link; Nettavisen comment-section blurb), 1 unsure, 9 correct → walls live in the bottom ~1 % → BACKLOG filter.
  Live re-fetch of 20 random articles: the stored text's middle is on the live page **20/20**; start and end also
  match 14/20 (edge misses = bylines/prefixes formatted differently, or articles edited since ingest).

## 2 · Clean & understand — DONE 2026-10-11 (A44 corrected to v2 the same day)
Rules accepted: A34, A36, A40 (incl. the ≥ 10 exception), A42, A43, A44 v2; A35 accepted for MiniLM.
Re-verify at the OpenAI embedding switch (A45): re-run the 2.1/2.2/2.3 evals on the 200 labelled pairs, recalibrate.

## 2.2 Embed — verified by Claude 2026-10-11
- [x] **Language gap is small:** labelled same-event pairs score median 0.77 within a language vs 0.75 across languages;
  100 % vs 91 % clear the 0.6 threshold directly (different-event pairs: median 0.51–0.55 either way). Cost: ~9 % of
  cross-language matches rely on clustering to join them.
- [x] **Outlet fingerprint is mild:** 14 % of an article's 5 nearest neighbours are from its own outlet (chance ≈ 4 %),
  mostly the outlet's own follow-ups; harmless because pairs are only formed across outlets.
- Topic-vs-event confusion is covered by the 2.3 check below (12 % of deployed pairs not the exact same event).

## 2.3 Stories — do matched articles cover the same event? (verified by Claude 2026-10-11)
- [x] **As deployed** (100 random pairs the judge would be asked to compare, run `2026-10-11T10:34:36Z_average_0.6`):
  **88 same event, 10 same story at a different moment, 2 different** → strict precision 88 % (95 % CI ≈ 80–93 %),
  same-story 98 %. The 2 errors: two different bridge stories; a multi-topic TV live page (TF1 "LCI Live").
- [x] **Threshold sweep** on 100 pairs stratified by similarity (labels: `data/eval/same_event_pairs.jsonl`,
  `labeler: claude`): average linkage 0.60 → P 0.92 / R 0.74 / F1 0.82; 0.55 → P 0.85 / R 0.87 / F1 0.86;
  components 0.60 → P 0.61 (confirms ADR-004). **Kept 0.60: precision first** — a wrong pair feeds the judge garbage,
  a missed pair only costs coverage.
- [x] After the A38 clean-up (HTML stripped from 1,221 summaries, rundowns excluded; run `2026-10-11T11:40:27Z`):
  average 0.60 → P 0.92 / R 0.76 / F1 0.83; 0.55 → P 0.89 / R 0.89 / F1 0.89; comparable pairs 5,357 → 5,765.
  Threshold kept at 0.60 for now: with 100 labels, 0.92 vs 0.89 precision is ~1 pair; revisit with more labels.
- [ ] Maintainer: optional spot-check of 10 of Claude's labels (`set`/`tag` fields in the two label files).

## Plane with ovals (2026-10-11)
- [ ] `bash scripts/start.sh` → open http://localhost:4200 → world plane with 10 country ovals; click a country → its outlets.
- [ ] `uv run python -m pipeline.aggregate.preview` → oval coverage close to 1 − e^(−k²/2) (39 % for k = 1).
- [x] k = 1 sd accepted for now (A25, 2026-10-11) — [ ] decide and whether the "skewed uncertainty at the extremes" caveat (A27) needs asymmetric shapes.
- Switch projects: `~/dev/pf/scripts/stop.sh` ↔ `~/dev/BasedNews/scripts/stop.sh`.

## 3 · Measure — judge and measuring loop
- [ ] Set a monthly hard limit in the OpenAI dashboard (e.g. $12).
- [x] Key in `.env`, models available (gpt-5.4-mini/nano, text-embedding-3-small/large) — checked 2026-10-11.
- [x] **Judge reliability** run 2026-10-11: v1 failed (length bias, mirror 0.20) → v2 adopted (mirror 1.00/0.91, length
      0.32/0.59, null pairs 1.00); see A46. Cost so far $0.76.
- [x] v3 (forced choice A/B/na) evaluated 2026-10-11: 1.4–1.8× more usable verdicts per dollar than v2;
      length bias gone (0.54/0.60); order bias ~0.35 handled by β. Spend so far $1.22.
- [ ] (repeat after any prompt/model change) `uv run python -m pipeline.judge.eval --pairs 60` → `reports/m7_judge_eval_v4.md`.
      All targets met? If not: read which (order, retest, mirror, null pairs) before measuring. Optional A11 check:
      `--model gpt-5.4-nano` and compare.
- [ ] **Embeddings** (≈ $0.03): `uv run --group ml python -m pipeline.embed.calibrate --model openai:text-embedding-3-small`
      → precision/recall vs MiniLM and the mapped thresholds; if they look right, add
      `BASEDNEWS_EMBED_MODEL=openai:text-embedding-3-small` to `.env` and re-run 2.1 → 2.2 → 2.3.
- [x] **Dry run:** `uv run python -m pipeline.measure.run --dry-run` → the first planned prompt and the cost estimate.
- [x] **First measurement (E4):** `uv run python -m pipeline.measure.run --budget-usd 1` → verdict counts, median outlet sd,
      judge order bias β; spend shown against the monthly cap.
- Without a key everything above runs with `--judge simulated` (verdicts kept apart, purpose "simulation").

## E4 — first real measurement (2026-10-11, $2.00) — see docs/experiments/e4_results.md
- [x] Pre-registered, run, analysed. 3 of 4 checks missed (AllSides 0.38, contrasts 7/9, split-half 0.51); nano ✅.
- [x] Next step chosen: fix the social definition → prompt v4. E4b (same pairs): AllSides 0.40 → 0.69. v4 active.
- [ ] Open: social length lean (0.73) and Fox News reading slightly liberal (docs/experiments/e4b_results.md).

## E6 — v4 + hierarchical model on fresh stories (2026-10-11, $0.49) — docs/experiments/e6_results.md
- [x] AllSides 0.38 (E4, v3 flat) → 0.60 (all v4 data, hierarchical) ✅; split halves 0.56 ✅; new planner 115 vs 61 distinct articles per 100 calls.
- [ ] Maintainer: spend the remaining $1.01 on other countries now, or keep it for next week's US/UK stories.

## A51 — full call log (built 2026-10-11)
- [x] Unit tests with a fake client: request stored exactly, full response, retries, failed call logged, no API key in
  the log, simulated verdicts not logged (`tests/test_calllog.py`, 4 tests). Simulated measure loop runs end to end.
- [ ] After the next paid run: `uv run python -m pipeline.judge.calllog` → "N of N paid calls have a full log ✅",
  integrity ✅, storage per call a few kB.

## 5 · Operate — job runner, export, retention, SQLite-in-S3, Docker (built 2026-10-12)
Verified by Claude: 65 unit tests pass (`tests/test_job.py`, `test_storage.py`, `test_retention.py`: records, gates,
exit codes, key redaction, a crash inside a write transaction, conditional upload against a second writer, lake before
delete, push-db never overwrites). On a copy of the real database: `export` (693 v4 calls, 2.6 kB gzip, gate OK),
`stories` (gate OK: 701 multi-outlet stories, largest story 0.8 %), `retention --apply` (107 → 93 MB, lake 2.9 MB,
a second run has nothing to do), `measure` without a budget is refused. The site builds with and without the export.
- [ ] `uv run python -m pipeline.job list` and `uv run python -m pipeline.job export`, then `bash scripts/start.sh` →
      the plane has a **Latest fit** button with the "not validated" banner. Good: 10 countries, ovals, no text.
- [ ] `uv run python -m pipeline.job retention` (dry run) → the plan reads sensibly; nothing changes.
- [ ] `uv run python -m pipeline.job history` → one line per step with status and the gate's numbers.
- [x] Docker (Claude, 2026-10-12): slim and ML images build; through compose against a copy in `data/state`, export,
      stories and embed ran, passed their gates, uploaded the database and their records; `history` reads the stored
      copy. torch is the CPU build (no CUDA). Try it: `docker compose run --rm job history`. CI builds the slim image.
- [ ] Decide A54: should the export go public on the deployed site (labelled "not validated"), or stay private until
      validation? Default: private (`BASEDNEWS_PUBLISH_POSITIONS` unset).
- [ ] Decide: the backup and legacy tables (`articles_legacy` 25 MB, `articles_text_backup` 3 MB,
      `articles_shared_text` 2 MB) — keep, move to the lake, or drop? Retention leaves them alone.
- [ ] Note: 300 v4 measure calls (2026-10-11 16:31–16:33) have no saved fit after them: that run stopped before its
      final fit. The export refits from all verdicts, so the site is unaffected; the retention refit check (A52) can
      compare again after the next measure run saves positions.

