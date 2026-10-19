# E8 — local embeddings for the history: results (2026-10-18)

Pre-registration `docs/experiments/e8_prereg.json` (amended before any result: the clustering sweep covers each model's
own range, ±0.08 around its best pair-level threshold, because the full 0.30–0.90 sweep did not finish). Pooled blind
test as E7b: 40 random anchors from the live 7-day window (60,778 articles), each model's top 4 other-outlet articles,
448 pairs, labelled blind by Claude (104 same story). 95 % intervals by bootstrap over anchors. `reports/e8.json`.

| model | AUC (M1) | threshold | precision | recall | F1 | F1 − live's (M4) |
|---|---|---:|---:|---:|---|---|
| OpenAI text-embedding-3-small @512 (live) | 0.942 [0.88, 0.98] | 0.56 | 0.85 | 0.65 | 0.74 [0.60, 0.86] | |
| **Qwen3-Embedding-0.6B** | **0.982 [0.96, 1.00]** | **0.54** | 0.92 | 0.75 | 0.83 [0.69, 0.92] | +0.01 … +0.17 |
| bge-m3 | 0.974 [0.95, 0.99] | 0.58 | 0.98 | 0.74 | 0.84 [0.72, 0.93] | +0.04 … +0.19 |
| multilingual-e5-large-instruct | 0.86 [0.74, 0.95] | 0.86 | 0.92 | 0.52 | 0.66 [0.48, 0.81] | −0.17 … −0.01 |

**Decision (pre-registered rule):** the history model is the local model with the highest AUC — **Qwen3-Embedding-0.6B,
threshold 0.54** — and it is valid: its F1 is not worse than live's (the interval lies above 0). bge-m3 is a near tie.
Speed: each model embedded the 64,871 texts in ≈ 2 minutes on the RTX 5080.

**Caveats.** One labeller (Claude), 40 anchors from one week. The test texts are what live embeds (headline + RSS
description, or the first 1,000 characters of the text when there is none); the history has headline + description
only, so the threshold is checked on the history's own story sizes before the pilot is judged.
