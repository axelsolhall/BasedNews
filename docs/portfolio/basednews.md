# BasedNews — material for a CV, a portfolio page or an application

Reusable text and numbers, each traceable to the repository. Figures are in `docs/img/`.

## One line
An AI system that measures how the news of 41 countries leans — 1.9 M LLM comparisons of how outlets frame the same
event, 2015 to today, judged daily and shown on a live site.

## Short (≈ 50 words)
BasedNews places 228 news outlets in 41 countries on an economic and a social axis by having an LLM judge compare how
two outlets frame the same event, names hidden. A Bayesian ranking model turns the verdicts into positions with
uncertainty; eleven years of history were judged on a gaming GPU by a fine-tuned open model.

## Medium (≈ 150 words)
BasedNews measures media framing at scale. It collects ≈ 24,000 articles a day from 228 outlets in 30 languages,
groups articles about the same event with multilingual embeddings, and asks an LLM judge — outlet names hidden — which
of two articles frames the event further right or more conservatively. Each verdict updates a Bayesian
Bradley–Terry model of every outlet's position and uncertainty, and an active-learning planner chooses the next
comparisons where the order of two outlets is most in doubt. To cover 2015–2026 without ≈ $930 of API calls, I
distilled the paid judge into a LoRA-tuned Gemma 4 12B on $3.58 of its verdicts and ran 1.9 M comparisons on a local
RTX 5080. The pipeline runs serverless on AWS (Step Functions, Fargate, S3, CloudFront) for a few dollars a month,
every step with its own quality gate. Positions agree with outside ratings (AllSides ρ +0.84).

## CV bullets
- Built an end-to-end ML pipeline that measures the political framing of 228 news outlets in 41 countries from
  1.9 M LLM pairwise judgments (Python, Bayesian Bradley–Terry, active learning); live site with 11 years of history.
- Distilled a paid LLM judge into a LoRA-tuned open model (Gemma 4 12B) on $3.58 of labels, raising agreement on the
  hard axis from κ 0.21 to 0.49, and served it with vLLM at ≈ 51,000 calls/hour on one consumer GPU.
- Ran the production pipeline serverless on AWS (Terraform, Step Functions, Fargate, S3, CloudFront) for ≈ $1/month
  of compute, with a quality gate per step, self-healing state in S3 and CI/CD via GitHub Actions OIDC.
- Validated against independent ratings (AllSides ρ +0.84; Reuters Institute DNR ρ +0.52) with
  pre-registered experiments and blind, pooled test sets.

## Numbers (with their source)
| | | source |
|---|---|---|
| outlets · countries · languages | 228 · 41 · 30 | `data/outlets.json` |
| history articles | 11.4 M, 2015-11 → 2026-10 | `data/history/history.sqlite` |
| history verdicts | 1,923,417 | `judge_calls`, purpose `history` |
| live articles | ≈ 24,000 a day | ingest records, 2026-10-24 → 30 |
| drawn on the site | 168 outlets (≥ 5,000 comparisons) | `reports/threshold.md` |
| agreement | AllSides ρ +0.84 · DNR ρ +0.52 | `reports/threshold.md`, `reports/external_check.md` |
| local judge | κ 0.21 → 0.49 (social axis, vs gpt-5.4-mini) · ≈ 51,000 calls/hour | `docs/experiments/e13_results.md`, `docs/LOCAL_LLM.md` |
| training labels | $3.58 of gpt-5.4-mini verdicts (3,085) | `docs/experiments/e11_results.md` |
| cost | ≈ $1/month AWS compute · ≈ $2.50 embeddings · ≤ $4.50 live judging | `README.md` (§ 5, costs) |
| tests | 228 | `uv run python -m pytest -q` |

## Skills it shows
LLM evaluation and distillation (LoRA, vLLM) · Bayesian modelling (Bradley–Terry, Laplace updates) · active learning ·
multilingual NLP (embeddings, clustering) · data engineering (SQLite, Parquet lake, retention) · cloud and MLOps (AWS
serverless, Terraform, Docker, CI/CD) · data visualisation (D3, Canvas) · experimental method (pre-registration,
blind pooled tests).

## Figures
- `docs/img/site.png` — the live site: countries' paths since 2015.
- `docs/img/concept.png` — two articles, names hidden, judged on two axes.
- `docs/img/local_judges.png` — local judges against the paid judge.
- `docs/img/cost.png` — what the history would have cost on the API.
