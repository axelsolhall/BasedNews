# Outliers in the external check — what is behind them (2026-10-26, night)

Follows `reports/external_check.md` (A78). History positions through 2023-04-05 (judging paused: PC off).
Read-only analysis; nothing in the pipeline or the database was changed.

## 1. The outliers are outlets with thin or unrepresentative history samples

| outlet | history articles (non-junk) | what the sample actually is |
|---|---:|---|
| Telegraph | 2,851 | Formula 1, recipes, books, Olympics, boxing, culture — its accessible sections, hardly any politics; the judge gives a direction on only 16 % of its pairs (median outlet 41 %) |
| NYT | 556 | mostly **cn.nytimes.com (Chinese edition)** and old blogs |
| Washington Post | 54 | one week (2019-01-01…08) of AP wire copy; econ −1.45 rests on **9** verdicts, frozen since (no data → no movement) |
| Le Monde | 72 | the rest of its ≈ 80k rows are "Client Challenge" bot walls (A76) |
| Süddeutsche | 4,887 | mostly the dpa ticker (`/news/politik/…`), only 147 calls |
| Libération | 27 | — |

Every other outlet in the DNR/AllSides sets has ≥ 19,000 articles. Daily Mail, Sky News, De Telegraaf and Irish
Independent have **no** verdicts at all (403 / reset, A33) and were already left out.

The UK "reversal" is the Telegraph alone: Guardian (+0.04 social) is left of BBC (+0.17), as DNR has it; the
Telegraph (−0.23) is measured on sport and culture pieces, where its angle is often the liberal one (equal pay,
BLM, a feminist obituary). Read 25 of its social verdicts by hand: where the pair had a real angle, ≈ 11 of 14 calls
looked right to me (wrong: Lineker, Greta's parents, Fay Weldon); ≈ 10 were toss-ups with no angle that got decided
anyway.

**Sensitivity (cutoff picked after seeing the data — the gap between 4,887 and 19,389 articles):**

| | all outlets | without the six thin ones |
|---|---|---|
| DNR within country, econ + social | +0.25 [−0.03, +0.44], n 63 | **+0.52 [+0.14, +0.76]**, n 57 |
| AllSides, econ + social | +0.79 [+0.42, +0.94], n 12 | **+0.88 [+0.55, +0.99]**, n 10 |
| US alone (DNR, social) | +0.71 | +0.99 |

## 2. The 2015-11 → 2017-10 verdicts were asked in a skewed order, and the judge has an order bias

- Calls made 2026-10-23/24 (history days 2015-11-01…2017-10-01, 292k calls, 26 % of all) did not randomise which
  article is shown first: Breitbart first 72–78 %, NY Post 73–76 %, Fox 60–67 %; RTBF 26–32 %, elDiario 34–38 %.
  From 2026-10-25 the order is random (per-outlet first-share consistent with chance: mean z² 0.92).
- The judge (G4-1/3) prefers one side by position even with random order: the first article is judged more
  left/liberal ≈ 56 % of the time; asked "which is more LEFT?" it picks the first 67 % on econ.
  P(first judged more right): econ 0.537 (positive) / 0.333 (negative); social 0.459 / 0.423.
- The independent model (the maintainer's spec, 2026-10-25) has no order term, so the old-order verdicts pull the
  outlets shown first toward liberal: a rebuild in memory (reproduces stored positions, r 0.9998) with that span's
  order effect removed moves Breitbart +0.42/+0.74 → +0.74/+1.11, Fox +0.37/+0.43 → +0.63/+0.62, NY Post → +0.69/+0.95,
  elDiario −0.19/−0.38 → −0.33/−0.57 (econ/social). Ranks change less: DNR (all) +0.25 → +0.31, AllSides unchanged.

## 3. One verdict carries very little; positions come from many

Direct same-country verdicts agree with the DNR order 50.8 % (n 35,725 social), rising with the audience gap:
gap ≤ .05: 49.7 % · .05–.10: 51.4 % · .10–.20: 50.7 % · > .20: 53.4 % (±1.7). Italy's outlets split ≈ 50/50 on
every pair. DNR's own spread is tiny in Finland, Ireland, Netherlands, Poland, Czechia (≤ 0.09 between brands —
near its sampling error), so those countries' reference ranks are mostly noise.

## Open — the maintainer's call (nothing done)

- **Thin outlets:** keep on the plane / mark "too little data" / leave out of tests and centres. cn.nytimes.com
  being counted as NYT looks like a bug in the URL → outlet match.
- **Old-order span:** re-judge 2015-11…2017-10 with random order (≈ 292k calls; at today's ≈ 44k calls/hour
  ≈ 7 GPU hours), or keep as is. (An order term in the model would also absorb it, but your spec leaves it out.)

Scripts here re-run it (from the repo root, `PYTHONPATH=. uv run python reports/outliers/<script>.py`); they read an
extract of the history verdicts, one row per call: `sqlite3 reports/outliers/v.sqlite < reports/outliers/extract.sql`
(not committed). coverage.csv = per-outlet articles, calls, decided shares, latest positions.
