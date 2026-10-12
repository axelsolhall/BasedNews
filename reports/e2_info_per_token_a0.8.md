# E2 — information per token

Seeds 3 · token budget 2,000,000 · summary discrimination a=0.8 · cost/call: full 2,700 tok, summary 540 tok

## At fixed spend (mean over seeds)

| Mode | tokens | calls | full-text share | Spearman | max outlet sd | max country sd | 90 % CI coverage |
|---|---|---|---|---|---|---|---|
| elo_random | 500,000 | 436 | nan% | 0.722 | nan | nan | nan |
| elo_random | 2,000,000 | 1756 | nan% | 0.937 | nan | nan | nan |
| bt_random | 500,000 | 436 | 27% | 0.759 | 0.699 | 0.296 | 0.86 |
| bt_random | 2,000,000 | 1756 | 28% | 0.939 | 0.393 | 0.213 | 0.93 |
| bt_variance | 500,000 | 230 | 71% | 0.754 | 0.697 | 0.318 | 0.88 |
| bt_variance | 2,000,000 | 1003 | 68% | 0.921 | 0.387 | 0.235 | 0.93 |
| bt_cost_aware | 500,000 | 916 | 0% | 0.895 | 0.432 | 0.248 | 0.91 |
| bt_cost_aware | 2,000,000 | 3700 | 0% | 0.973 | 0.265 | 0.189 | 0.96 |

## Tokens to reach target uncertainty (median; '>' = not reached)

| Target | bt_random | bt_variance | bt_cost_aware | saving cost-aware vs random | vs variance |
|---|---|---|---|---|---|
| max_sd ≤ 0.4 | >2,000,000 | 1,909,980 | 604,800 | n/a | 68 % |
| max_sd ≤ 0.3 | >2,000,000 | >2,000,000 | 1,414,800 | n/a | n/a |
| max_country_sd ≤ 0.25 | 1,062,720 | 1,411,020 | 549,180 | 48 % | 61 % |
| max_country_sd ≤ 0.2 | >2,000,000 | >2,000,000 | 1,596,780 | n/a | n/a |
