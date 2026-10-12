# E6 results — prompt v4 + hierarchical model + article-aware planner (2026-10-11, $0.49 of $1.50)

Pre-registration `docs/experiments/e6_prereg.json`. Full tables: `reports/e6_results.md`. Not a claim (pipeline phase).
The all-countries part stopped early (tool error) after 300 calls; $1.01 unspent.

| | E4 (v3, flat model) | E4b (v4, same pairs; flat / hierarchical) | now (v4, hierarchical, E4b + E6) |
|---|---|---|---|
| AllSides vs social, 13 US/UK outlets | 0.38 | 0.69 / 0.68 | **0.60** (target ≥ 0.60 met, just) |
| split halves US/UK (mean of 30) | 0.43 | 0.44 / 0.50 | **0.56** |
| contrasts decided / confirmed | 9 / 7 (2 contradicted) | – | 2 / 2 (none contradicted; most undecided) |

The gain over E4 comes from the prompt (v3 → v4); the hierarchical model barely moves the ranking but makes the
intervals honest. 0.60 vs 0.68 is within noise for 13 outlets (one swapped pair moves Spearman by ~0.1).

Planner (descriptive, E4 flat vs E6 article-aware): distinct articles per 100 calls 61 → 115; mean uses per article
3.3 → 1.7; max 29 → 10; calls re-using a judged article 82 % → 59 %.

Reading: with v4 and honest article-level uncertainty, agreement with AllSides reaches the pre-registered bar; most
contrasts become undecided rather than wrong — the limit is distinct articles per outlet, i.e. more days of data.
Every US/UK pair available this week now has a v4 verdict.
