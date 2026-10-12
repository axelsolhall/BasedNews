# E3 — semi-real replay

Real data: event run `2026-10-11T10:34:36Z_average_0.6` · 3330 events · 71 outlets in 10 countries · 5,357 comparable pairs (4,347 cross-country) · real text availability and token costs.
Simulated: true positions ~ N(0,1) per outlet per axis; judge verdicts (one call → both axes). Seed 0, budget 750,000 tokens.

| Strategy (mean of 5 seeds) | tokens | calls | Spearman econ | Spearman social | max outlet sd | max country sd | 90 % coverage |
|---|---|---|---|---|---|---|---|
| random | 187,500 | 184 | 0.385 ± 0.093 | 0.444 ± 0.046 | 1.000 | 0.457 ± 0.009 | 0.90 |
| random | 375,000 | 384 | 0.542 ± 0.073 | 0.588 ± 0.059 | 1.000 | 0.432 ± 0.014 | 0.90 |
| random | 750,000 | 815 | 0.641 ± 0.036 | 0.702 ± 0.035 | 1.000 | 0.373 ± 0.015 | 0.89 |
| cost_aware | 187,500 | 344 | 0.603 ± 0.100 | 0.602 ± 0.055 | 1.000 | 0.365 ± 0.005 | 0.90 |
| cost_aware | 375,000 | 696 | 0.714 ± 0.046 | 0.749 ± 0.064 | 1.000 | 0.316 ± 0.004 | 0.88 |
| cost_aware | 750,000 | 1372 | 0.804 ± 0.044 | 0.824 ± 0.053 | 1.000 | 0.279 ± 0.008 | 0.89 |

Tokens until max_sd ≤ 0.6: random > 750,000 · cost-aware > 750,000

Tokens until max_sd ≤ 0.5: random > 750,000 · cost-aware > 750,000

Tokens until max_country_sd ≤ 0.3: random > 750,000 · cost-aware 486,866
