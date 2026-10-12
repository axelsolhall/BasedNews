# E2 — information per token

Seeds 3 · token budget 2,000,000 · summary discrimination a=0.4 · cost/call: full 2,700 tok, summary 540 tok

## At fixed spend (mean over seeds)

| Mode | tokens | calls | full-text share | Spearman | max outlet sd | max country sd | 90 % CI coverage |
|---|---|---|---|---|---|---|---|
| elo_random | 500,000 | 436 | nan% | 0.530 | nan | nan | nan |
| elo_random | 2,000,000 | 1756 | nan% | 0.884 | nan | nan | nan |
| bt_random | 500,000 | 436 | 27% | 0.583 | 0.878 | 0.343 | 0.85 |
| bt_random | 2,000,000 | 1756 | 28% | 0.879 | 0.595 | 0.253 | 0.91 |
| bt_variance | 500,000 | 210 | 84% | 0.708 | 0.939 | 0.345 | 0.88 |
| bt_variance | 2,000,000 | 1042 | 64% | 0.895 | 0.542 | 0.247 | 0.90 |
| bt_cost_aware | 500,000 | 630 | 11% | 0.697 | 0.667 | 0.315 | 0.91 |
| bt_cost_aware | 2,000,000 | 2014 | 21% | 0.897 | 0.393 | 0.229 | 0.89 |

## Tokens to reach target uncertainty (median; '>' = not reached)

| Target | bt_random | bt_variance | bt_cost_aware | saving cost-aware vs random | vs variance |
|---|---|---|---|---|---|
| max_sd ≤ 0.4 | >2,000,000 | >2,000,000 | 1,955,340 | n/a | n/a |
| max_sd ≤ 0.3 | >2,000,000 | >2,000,000 | >2,000,000 | n/a | n/a |
| max_country_sd ≤ 0.25 | >2,000,000 | >2,000,000 | 1,412,640 | n/a | n/a |
| max_country_sd ≤ 0.2 | >2,000,000 | >2,000,000 | >2,000,000 | n/a | n/a |
