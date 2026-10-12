# E2 — information per token

Seeds 5 · token budget 2,000,000 · summary discrimination a=0.6 · cost/call: full 2,700 tok, summary 540 tok

## At fixed spend (mean over seeds)

| Mode | tokens | calls | full-text share | Spearman | max outlet sd | max country sd | 90 % CI coverage |
|---|---|---|---|---|---|---|---|
| elo_random | 500,000 | 453 | nan% | 0.655 | nan | nan | nan |
| elo_random | 2,000,000 | 1809 | nan% | 0.918 | nan | nan | nan |
| bt_random | 500,000 | 453 | 26% | 0.703 | 0.787 | 0.323 | 0.89 |
| bt_random | 2,000,000 | 1809 | 27% | 0.922 | 0.468 | 0.225 | 0.93 |
| bt_variance | 500,000 | 225 | 73% | 0.732 | 0.784 | 0.335 | 0.91 |
| bt_variance | 2,000,000 | 1060 | 64% | 0.907 | 0.438 | 0.234 | 0.92 |
| bt_cost_aware | 500,000 | 905 | 0% | 0.865 | 0.506 | 0.264 | 0.90 |
| bt_cost_aware | 2,000,000 | 3692 | 0% | 0.961 | 0.307 | 0.199 | 0.97 |

## Tokens to reach target uncertainty (median; '>' = not reached)

| Target | bt_random | bt_variance | bt_cost_aware | saving cost-aware vs random | vs variance |
|---|---|---|---|---|---|
| max_sd ≤ 0.4 | >2,000,000 | >2,000,000 | 926,640 | n/a | n/a |
| max_sd ≤ 0.3 | >2,000,000 | >2,000,000 | >2,000,000 | n/a | n/a |
| max_country_sd ≤ 0.25 | 1,408,320 | 1,518,480 | 602,640 | 57 % | 60 % |
| max_country_sd ≤ 0.2 | >2,000,000 | >2,000,000 | >2,000,000 | n/a | n/a |
