# P1 — rating core on the simulator

Seeds: 5 · budget: 3000 LLM calls · batch: 20 · world: 8 countries × 5 outlets, judge bias 0.3, article noise 0.3

## Calls needed to reach target uncertainty (median over seeds; '>' = not reached)

| Metric | Target | random | eig | variance | saving vs random |
|---|---|---|---|---|---|
| max_sd | 0.4 | 1146 | 986 | 765 | 33 % |
| max_sd | 0.3 | >3000 | >3000 | 1766 | n/a |
| max_country_sd | 0.25 | 780 | 766 | 586 | 25 % |
| max_country_sd | 0.15 | >3000 | >3000 | >3000 | n/a |

## End of budget (mean over seeds)

| Mode | mean outlet sd | max outlet sd | max country sd | Spearman | 90 % CI coverage | beta_hat (true 0.3) |
|---|---|---|---|---|---|---|
| random | 0.242 | 0.287 | 0.187 | 0.968 | 0.91 | 0.30 |
| eig | 0.235 | 0.280 | 0.199 | 0.978 | 0.89 | 0.31 |
| variance | 0.232 | 0.256 | 0.183 | 0.979 | 0.89 | 0.29 |
