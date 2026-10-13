# ADR-011 — Embeddings from the OpenAI API at 512 dimensions, chosen by an unbiased pooled test

**Status:** accepted 2026-10-12 · **Supersedes:** the local MiniLM as the active model in 2.1–2.3 (A12; MiniLM stays as
the baseline) · **Records:** A56, E7, E7b, finding F2

## Context
Stages 2.1–2.3 were built and signed off on a local multilingual MiniLM (384 dimensions). For production it means
torch and the model inside the job image: 2.2 GB instead of 0.43 GB, a slower start for every hourly task. The
maintainer chose the OpenAI API for embeddings; the open questions were the vector size and whether the switch costs
quality in story matching.

## Standard vs explored
| | Common practice | Here |
|---|---|---|
| choosing an embedding model | a public benchmark (MTEB), or a test set labelled from the current model's output | a pre-registered pooled test on our own stories: random anchors, each model's top candidates pooled at equal depth, labelled blind (E7b) |
| vector size | the model's full size | 512 of 1,536: measured equal on our task, a third of the storage (E7, E7b addendum) |
| thresholds after a switch | reuse the old ones | re-chosen per model on the fair set (A47): story threshold 0.56; the 2.1 thresholds by quantile mapping |
| repeated embedding calls | re-embed on every run | a vector cache by text hash: an hourly run pays only for new texts (A56) |

## Decision
`openai:text-embedding-3-small@512` is the default (`pipeline/settings.py`), with its profile in
`data/embedding_profiles.json`. MiniLM stays available as the local baseline (`uv sync --group ml`,
`BASEDNEWS_EMBED_MODEL`), and the image can still be built with it (`WITH_ML=1`).

## Evidence
- **E7** (200 labelled pairs, $0.045): 512 vs 1536 recall +0.015 [−0.015, +0.044]; the API's 512-d output equals the
  1,536-d vector truncated and re-normalised (mean cosine 0.99999). Its MiniLM comparison is **withdrawn**: every pair had
  been selected with MiniLM.
- **E7b** (free, 40 random anchors, 270 pooled pairs labelled blind): OpenAI 512 ahead of MiniLM on every measure —
  AUC 0.965 vs 0.925 (difference +0.04 [+0.002, +0.10]), precision@1 0.43 vs 0.35, clustering F1 0.71 vs 0.60.
  Addendum with 1536 at the same depth: AUC 0.964, not measurably better than 512.

## Consequences
- The job image is small (≈ 430 MB compressed); no GPU or torch in production.
- Embedding costs ≈ $0.01 a day (new articles) plus a one-time cache fill (≈ $0.004), inside the monthly AI cap (A48),
  which the encoder enforces before every paid batch.
- Vectors take ≈ 100 MB for a 30-day window; retention removes old vectors and unused cache entries (A52).
- Story matching depends on an external API: an outage stops the hourly refresh at the embed step's gate, and the site
  keeps its last good data (A53).
- A35 (one vector per article = mean over headline versions) was motivated by MiniLM's 128-token limit; OpenAI reads
  8,000 tokens, so concatenating versions is now possible. Kept as is until measured (BACKLOG).
- Lesson recorded as F2: a test set chosen by one of the compared models can decide the comparison.
