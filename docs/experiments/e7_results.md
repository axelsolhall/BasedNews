# E7 results — OpenAI embeddings: 512 vs 1536 dimensions vs MiniLM (2026-10-12, $0.045 of $0.10)

Pre-registration `docs/experiments/e7_prereg.json` (written 17:18 UTC, before the first embedding call; sha256
`acae3a99…`). Full tables and figure: `reports/e7_embeddings.md`, `reports/e7_embeddings.png`; calibration logs
`reports/e7_calibrate_512.txt`, `reports/e7_calibrate_1536.txt`. Analysis: `uv run python -m pipeline.experiments.e7`
(free, from the stored vectors). 200 labelled pairs (134 same story), 7-day window of 7,093 articles. Not a claim.

> **2026-10-12: the MiniLM-vs-OpenAI conclusion below is withdrawn.** The label set was selected with MiniLM, so it
> cannot rank MiniLM against another model. The size result (512 = 1536) stands: it compares two OpenAI sizes on the
> same pairs. The unbiased re-test is E7b (`docs/experiments/e7b_prereg.json`).

## Pre-registered questions

| | MiniLM 384 (baseline) | OpenAI 512 | OpenAI 1536 |
|---|---|---|---|
| threshold (operating point, A47 rule) | 0.60 | 0.62 | 0.60 |
| precision [95 %] | 0.96 [0.91, 0.99] | 0.96 [0.91, 1.00] | 0.98 [0.94, 1.00] |
| **recall [95 %]** | **0.83 [0.76, 0.89]** | **0.42 [0.34, 0.51]** | **0.41 [0.32, 0.49]** |
| F1 | 0.89 | 0.59 | 0.57 |
| vectors for a 30-day window | 74 MB | 98 MB | 295 MB |
| cost (window + calibration) | free (local) | $0.022 | $0.022 |

- **Q1 — does OpenAI reach MiniLM's recall at MiniLM's precision?** No. At precision ≥ 0.96 both OpenAI sizes find
  about half of the labelled same-story pairs MiniLM finds; the intervals do not overlap.
- **Q2 — is 512 within 0.03 of 1536?** Yes: recall 512 − 1536 = +0.015 [−0.015, +0.044]. The pre-registered rule
  says **512**.
- **Q3 — dimension curve (1536 truncated).** Threshold-free quality is flat from 256 up (pair AUC 0.84 → 0.855, best
  F1 0.885 → 0.898) and degrades only below 256 (AUC 0.81 at 64). Recall at the fixed precision jumps around (0.36–0.56)
  because that operating point sits on a steep, jagged part of the curve: with 200 pairs one pair moves it.
- **Q4 — is the API's 512 the 1536 vector cut to 512 and re-normalised?** Yes: mean cosine 0.99999 (minimum 0.997)
  over 7,093 articles. Buying 1536 and truncating locally gives every smaller size for free.

## Exploratory (added after the first numbers, flagged as such)

- **Threshold-free comparison.** Pair AUC (does a same-story pair have a higher cosine than a different-story pair):
  MiniLM 0.88, OpenAI 0.85 / 0.86. On the *band* pairs (sampled around MiniLM's threshold) the three are almost equal:
  0.92 / 0.91 / 0.92. The gap comes from the *deployed* pairs (0.79 vs 0.68): pairs that MiniLM's own clustering put
  together, so they favour MiniLM by construction.
- **The curves are shifted, not just worse.** OpenAI's cosines for same-story pairs are lower, so its useful
  thresholds lie lower (best F1 at 0.30–0.38 against MiniLM's 0.46). At precision ≈ 0.90: MiniLM recall 0.94 (0.52),
  OpenAI 512 recall 0.79 (0.46); at precision ≈ 0.88: OpenAI 512 recall 0.88 (0.42). The 512 model's best F1 sits at
  the grid's lower edge (0.30), so its true optimum may be lower still; the grid was fixed in advance and not changed.

## Reading

1. 512 dimensions lose nothing measurable against 1536 for this task, and cost a third of the storage: the size
   question is settled.
2. On this label set, OpenAI embeddings do **not** beat the local MiniLM; at the strict precision the 2.3 rule uses,
   they find half the same-story pairs. Fewer story pairs means fewer comparisons for the judge to choose from.
3. The label set cannot settle MiniLM vs OpenAI fairly: every labelled pair was chosen with MiniLM (its similarity band
   or its clusters), so pairs MiniLM missed entirely are absent, and Claude labelled them. On the least biased part
   (band) the two are tied.

## What it changes
- If OpenAI: use `openai:text-embedding-3-small@512`, but not at the A47 rule's threshold (0.62): that halves story
  recall. A threshold around 0.44–0.46 trades precision 0.96 → ≈ 0.89–0.90 for recall ≈ 0.80–0.88.
- A fair MiniLM-vs-OpenAI test needs labels not selected by either model: pairs where the two models disagree plus
  random same-window pairs (free to draw; labelling ≈ 20 minutes). Recorded in BACKLOG.
- Superseded by E7b (docs/experiments/e7b_results.md): on an unbiased pooled test OpenAI @512 is ahead; it is the
  default from 2026-10-12, story threshold 0.56.
