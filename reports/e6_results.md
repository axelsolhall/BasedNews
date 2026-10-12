# E6 — fresh stories, prompt v4, article-aware planner + hierarchical model

Pre-registration `docs/experiments/e6_prereg.json` (sha256 `9c30de1342b06b1b…`). E6 calls: **355**, **$0.49**.

## Q1 — does the new planner spread verdicts over more articles?

| | E4 (flat planner, v3) | E6 (article-aware, v4) |
|---|---:|---:|
| calls | 1095 | 355 |
| distinct articles | 663 | 407 |
| distinct articles per 100 calls | 61 | 115 |
| mean uses per article | 3.30 | 1.74 |
| max uses of one article | 29 | 10 |
| calls that reuse an already-judged article | 82% | 59% |

Descriptive: prompt and stories differ between the runs.

## Q2–Q4

| question | result | target | |
|---|---|---|---|
| Q2 AllSides vs social — E6 only (fresh) | undecided: 5 rated outlets with ≥ 5 decisive verdicts · ICC 0.60 (σ_a 0.28, τ 0.34) | ≥ 0.60 | – |
| Q2 AllSides vs social — all v4 (E4b + E6) | Spearman +0.60 on 13 outlets · ICC 0.16 (σ_a 1.41, τ 0.61) | ≥ 0.60 | ✅ |
| Q3 contrasts (social, all v4) | 2 of 2 decided confirmed | ≥ 80 % | ✅ |
| Q4 split halves (US/UK, all v4) | mean 0.56 over 30 splits (range 0.01–0.80) | ≥ 0.50 (exploratory) | ✅ |

| country | expected (social): more conservative than | P | outcome |
|---|---|---|---|
| US | Breitbart > The New York Times | 0.69 | undecided |
| US | Fox News > NPR | 0.60 | undecided |
| US | New York Post > The Washington Post | 0.74 | undecided |
| UK | GB News > The Guardian | 0.84 | confirmed |
| UK | Daily Mail > The Guardian | 0.88 | confirmed |
| UK | The Telegraph > The Guardian | nan | no data |
| DE | Bild > taz | 0.57 | undecided |
| DE | Die Welt > taz | 0.58 | undecided |
| DE | Frankfurter Allgemeine > Süddeutsche Zeitung | 0.56 | undecided |
| FR | Le Figaro > Libération | 0.53 | undecided |
| FR | Le Figaro > Le Monde | 0.64 | undecided |
| ES | OKDiario > elDiario.es | nan | no data |
| ES | ABC > El País | 0.50 | undecided |
| ES | El Mundo > El País | 0.57 | undecided |
| IT | Libero Quotidiano > la Repubblica | 0.72 | undecided |
| IT | Libero Quotidiano > La Stampa | 0.69 | undecided |
| DK | Jyllands-Posten > Politiken | 0.64 | undecided |

## Also

- 'na': econ 81%, social 68% · cost per decisive verdict $0.0026
- judge order bias β (social, all v4): -0.52
