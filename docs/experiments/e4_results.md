# E4 results — first real measurement (2026-10-11)

Pre-registration: `docs/experiments/e4_prereg.json` (sha256 5670842c…, written before any measurement call).
Full numbers: `reports/e4_validation.md` + `reports/e4_validation.png`. This validates the instrument; it is not a claim.

## What was spent
| part | calls | cost |
|---|---:|---:|
| nano vs mini (40 pairs) | 40 | $0.02 |
| US + UK pairs (every pair this week's stories offered) | 338 | $0.51 |
| all 10 countries, planner | 1,095 | $1.47 |
| **total** | **1,473** | **$2.00** |
Deviation from the plan: the US/UK pool ran out at $0.51 (planned $1.20); the rest went to all countries.

## Pre-registered checks
| check | result | target | |
|---|---|---|---|
| AllSides vs our social position (13 rated outlets) | Spearman 0.38 (p 0.10) | ≥ 0.60 | ❌ |
| pre-written contrasts | 7 of 9 decided confirmed (78 %), 11 undecided | ≥ 80 % | ❌ (one short) |
| split halves, US/UK social | Spearman 0.51 | ≥ 0.70 | ❌ |
| gpt-5.4-nano agrees with mini | 0.82 / 0.70 vs mini's own retest 0.78 / 0.80 | ≥ retest − 0.10 | ✅ (social exactly on the bar) |

Confirmed strongly: GB News, Daily Mail and The Telegraph more conservative than The Guardian (P 0.92–1.00); Fox News
than NPR (0.98); Le Figaro than Libération and Le Monde; Libero than la Repubblica. Contradicted: FAZ > SZ (0.05),
El Mundo > El País (0.18). Undecided (too little data): most of SE, NO, DK, ES, DE.

## What we learned
1. **There is signal, but not enough yet.** The extremes land where everyone expects (The Guardian and NPR liberal;
   Breitbart and GB News conservative); two outliers break the correlation: **The New York Times comes out conservative
   (+1.1)** — it was judged the more conservative article in 19 of its 28 social verdicts — and **Fox News slightly
   liberal (−0.4)**.
2. **Likely cause for NYT (hypothesis, not tested):** the CONSERVATIVE definition includes "trusts police, military and
   authorities" (and LIBERAL "scrutinises state power"). Straight news that leans on official sources reads as
   "trusting authorities". Sky News (+0.6) and BBC (+0.1) fit the same pattern.
3. **The economic axis is almost empty:** "no angle" on 90 % of economic questions (social 48 %). Economic positions
   are still mostly the prior; nearly all information this week is social.
4. **Too little data per outlet:** median 39 comparisons per outlet, ≈ 20 decisive social verdicts. The split-half
   0.51 says differences among the seven "Lean Left" US outlets are within noise. One week of US/UK stories gives only
   ~340 pairs.
5. **The judge leans toward the second-shown article** (picked in 61 % of decisive verdicts; β ≈ −0.9). Random order
   lets the model remove it, but it costs information.
6. **Cost:** $0.0014 per call, $0.0022 per decisive verdict. gpt-5.4-nano answers like mini at ¼ of the price.

## Options for the next step (maintainer decides)
- **Fix the social definition** (drop or reword the "authorities / state power" lines so quoting officials is not
  itself conservative), re-run the judge eval and a ~$1 replication: do NYT, Sky and BBC move?
- **Diagnose first** (~$0.05): re-ask the 28 NYT pairs with the v2 prompt (which gives reasons) to see why NYT reads
  conservative, before changing anything.
- **Economic axis:** ask it only on stories with an economic angle (topic filter), or accept that it needs weeks of data.
- **More data:** keep measuring weekly (~$1.50 a week at this rate, inside the $12 cap); re-run these same checks.
- **Cheaper judge:** switch to nano (¼ cost) once the definition is settled — re-run the eval on nano first.
