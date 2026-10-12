# ADR-009 — Measure outlets over their articles: hierarchical Bradley–Terry with article effects

**Status:** accepted 2026-10-11 · **Supersedes:** the flat outlet-level model in 3.3

## Context
E4c found that one article can carry a large share of an outlet's verdicts (NYT: 23 verdicts from 9 articles, one in
10). The flat Bradley–Terry model (and Elo) treats every verdict as independent evidence about the outlet, so one
article's coverage of one story can decide where the outlet lands, and its uncertainty is understated.

## Standard vs explored
| | Standard | Explored here |
|---|---|---|
| unit of evidence | the comparison | the article: θ_article = θ_outlet + ε, ε ~ N(0, σ_a²) |
| repeated comparisons of one article | full weight each time | diminishing returns, capped at one article's worth |
| variance components | fixed or ignored | outlet spread τ and article spread σ_a both estimated (Laplace evidence), averaged over a grid (Bayesian model averaging) |
| active selection | value of a comparison per outlet pair | value per article: a new article is worth more than the n-th look at a known one, from the covariance alone |

## Decision
`pipeline/rate/hierarchical.py` replaces the flat model in the measuring loop; `plan_joint_hier` plans over outlets,
β and articles. Inside a run the spreads are held at their best evidence values for speed; positions are reported from
the evidence-averaged fit. The flat model stays as the baseline in experiments.

## Consequences
- Honest uncertainty: real-data outlet sds widen (median ± 0.46 → ± 0.64) because one article says little about its outlet.
- More reproducible rankings: split halves agree 0.50 vs 0.44 (mean of 30 random splits, US/UK, E4b).
- Single-article outliers shrink toward the middle (NYT +1.10 → +0.49 on E4 data).
- Planning number: ±0.2 for an outlet needs on the order of 70 distinct judged articles (σ_a ≈ 1.7).
- Cost: a fit is now over outlets + articles (hundreds to thousands of parameters); fine at current scale, sparse
  solvers later.
- Known limitation: with few repeats per article the Laplace evidence underestimates σ_a (simulation: 0.22–0.40 for a
  true 0.5), so the hierarchical model still slightly under-covers (≈ 0.85 vs nominal 0.90).

See docs/FINDINGS.md (F1), A49, reports/e5_article_effects.md.
