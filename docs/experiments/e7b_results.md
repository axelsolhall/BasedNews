# E7b results — unbiased story-matching test: OpenAI @512 vs MiniLM (2026-10-12, free)

Pre-registration `docs/experiments/e7b_prereg.json` (written before sampling or labelling; sha256 `33a818da…`). Full
tables: `reports/e7b_pooled.md`, `reports/e7b_pooled.json`. Pool and labels: `data/eval/e7b_pool.jsonl`,
`data/eval/e7b_labels.jsonl`. Code: `pipeline/experiments/e7b.py`. Not a claim.

**Why it exists.** E7's labelled pairs had been selected with MiniLM, and on them MiniLM looked twice as good. That
comparison was invalid and is withdrawn. E7b uses pooled evaluation with equal depth, the standard way to compare
retrieval systems: 40 anchor articles drawn at random (no model involved), the top 4 other-outlet candidates of each
model per anchor, the union (270 pairs) labelled blind: shuffled, no model names, no scores. 80 pairs are the same
story; 18 of the 40 anchors have at least one.

| | MiniLM 384 | OpenAI 512 |
|---|---|---|
| precision@1 (best candidate is the same story) | 0.35 | **0.43** |
| precision@4 [95 %] | 0.34 [0.21, 0.48] | **0.37** [0.23, 0.51] |
| recall@4, of the pool [95 %] | 0.68 [0.60, 0.75] | **0.74** [0.67, 0.83] |
| AUC, threshold-free | 0.925 | **0.965** |
| 2.3 clustering at its threshold | 0.60 → P 0.86, R 0.46, F1 0.60 | 0.56 → P 0.90, R 0.59, F1 **0.71** |
| best F1 over the sweep | 0.80 (0.48) | **0.84** (0.44) |

- **AUC difference** (exploratory, paired bootstrap over anchors): +0.040 [+0.002, +0.103]; OpenAI higher in 98 % of
  resamples.
- **Clustering intervals** overlap: precision [0.60, 1.00] vs [0.77, 1.00], recall [0.23, 0.68] vs [0.41, 0.75]; 40
  anchors are few.
- **Pre-registered rule for OpenAI's story threshold** (highest F1 with precision ≥ 0.90): **0.56**. Now in
  `data/embedding_profiles.json`, replacing E7's 0.62, which was chosen on the MiniLM-selected set.

## Reading
1. On a test set neither model chose, OpenAI `text-embedding-3-small` at 512 dimensions matches stories better than
   the local MiniLM on every measure, by a small margin with wide intervals.
2. The same comparison on a MiniLM-selected set showed the opposite with a large margin (E7). The test set decided the
   winner, not the models: finding F2.
3. Decision (A56): OpenAI @512 is the default embedding model from 2026-10-12, story threshold 0.56. The story run
   rebuilt with it has 965 multi-article stories (MiniLM: 890) and passes the stories gate.

## Limitations
- Recall is relative to the pool: a same-story pair neither model ranked in its top 4 is not counted.
- The labeller is Claude; the maintainer may spot-check (VERIFY.md). Judgement calls were applied the same way to
  every pair: follow-ups of one decision count as the same story (a ban and the legal challenge to it), a sprint race
  and the main race do not, a preview and the announcement do not.
- One week of news, 40 anchors.

## Addendum — OpenAI @1536 at the same depth (asked after the run; not pre-registered)
1536's top 4 per anchor added 30 pairs to the pool. They were labelled blind, mixed with 30 relabels of existing pairs:
the relabels agreed 30/30 (`reports/e7b_relabel.json`). All three models on the 300 pooled pairs
(`reports/e7b_addendum.md`, `uv run python -m pipeline.experiments.e7b analyse --addendum`):

| | MiniLM 384 | OpenAI 512 | OpenAI 1536 |
|---|---|---|---|
| AUC [95 %] | 0.918 [0.83, 0.97] | 0.963 [0.93, 0.98] | 0.964 [0.93, 0.99] |
| precision@1 | 0.35 | 0.43 | 0.45 |
| recall@4, of the pool | 0.63 | 0.69 | 0.71 |
| clustering (threshold by the same rule) | 0.60: F1 0.59 | 0.56: F1 0.70 | 0.54: F1 0.73 |
| best F1 over the sweep | 0.79 | 0.84 | 0.83 |

1536 − 512 AUC: +0.001 [−0.003, +0.008]. 1536 is not measurably better than 512 (one anchor's top candidate differs;
the clustering F1 intervals overlap almost entirely), and it costs three times the storage. 512 stays, as E7 found.

