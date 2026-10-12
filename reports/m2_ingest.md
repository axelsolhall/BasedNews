# M2 ingest: coverage report

Generated 2026-10-11 by `uv run python -m pipeline.ingest.report`. Counts only, no article text (A10).

![M2 coverage](m2_coverage.png)

**Data.** *history* = archive.org backfill (1.3, ~4 articles per outlet per snapshot date, same dates for every outlet, A23); *live* = RSS ingest (1.2) since 2026-10-01.

**Pass criteria per outlet** (health of the ingest that runs from now on, so text and dates are checked on live-ingest articles): ≥ 5 articles published in the last 72 h · full text or summary on ≥ 90 % · parseable date on ≥ 95 %. Full text means ≥ 300 extracted characters (A19).

## Findings

- Outlets failing the pass criteria: 1 (Le Parisien)
- Undated or unparseable `published`: 0 articles; dated in the future: 0
- Backfill: 371 of 730 outlet-years not attempted yet (hatched in the figure)
- M3 (A34): 14 re-headlined copies merged, 190 articles carry a headline history; 22 live-blog/news-list entries keep headline + summary only
- M3 (A38): not counted as articles — 456 headline-only items, 17 multi-topic rundowns, 17 site/hub pages; 56 consent/paywall walls keep headline + summary only
- Highest boilerplate-suspect share (cookie/consent/subscribe words in the first 300 chars): NPR 2 %, Ekstra Bladet 2 %, Expressen 2 %

## By country

| Country | Outlets (ref / all) | Articles | Last 72 h | Last 14 d | History years (of 10, mean) | Full text | Summary only | Dated | Pass |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Norway | 5 / 6 | 512 | 366 | 413 | 4.0 | 94 % | 6 % | 100 % | 6/6 |
| Sweden | 5 / 6 | 742 | 570 | 644 | 4.8 | 74 % | 26 % | 100 % | 6/6 |
| Denmark | 5 / 7 | 400 | 279 | 295 | 4.3 | 94 % | 6 % | 100 % | 7/7 |
| Finland | 5 / 5 | 587 | 508 | 508 | 4.0 | 93 % | 7 % | 100 % | 5/5 |
| United States | 5 / 10 | 724 | 510 | 537 | 5.3 | 78 % | 22 % | 100 % | 10/10 |
| United Kingdom | 5 / 6 | 872 | 639 | 701 | 4.5 | 47 % | 53 % | 100 % | 6/6 |
| Germany | 5 / 9 | 1,808 | 1,409 | 1,612 | 5.2 | 74 % | 26 % | 100 % | 9/9 |
| France | 5 / 9 | 715 | 590 | 598 | 3.3 | 24 % | 76 % | 100 % | 8/9 |
| Spain | 5 / 7 | 1,372 | 1,176 | 1,244 | 4.9 | 89 % | 11 % | 100 % | 7/7 |
| Italy | 5 / 8 | 743 | 611 | 627 | 4.0 | 92 % | 8 % | 100 % | 8/8 |
| **All** | 50 / 73 | **8,475** | 6,658 | 7,179 | 4.5 | 75 % | 25 % | 100 % | 72/73 |

## By outlet

| Outlet | Country | Tier | DNR reach | Articles | history / live | 72 h | History yrs | Full text | Summary | Dated | Dup titles | Boilerplate suspect | Median chars | Status |
|---|---|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| NRK | Norway | reference | 53 % | 188 | 20 / 168 | 121 | 5 | 94 % | 6 % | 100 % | 1.1 % | 0 % | 2,076 | OK |
| VG | Norway | reference | 61 % | 37 | 19 / 18 | 18 | 5 | 100 % | 0 % | 100 % | 0.0 % | 0 % | 2,235 | OK |
| Aftenposten | Norway | extra | 20 % | 94 | 20 / 74 | 74 | 5 | 96 % | 4 % | 100 % | 0.0 % | 1 % | 1,272 | OK |
| Dagbladet | Norway | reference | 32 % | 86 | 13 / 73 | 65 | 4 | 87 % | 13 % | 100 % | 1.2 % | 0 % | 2,603 | OK |
| TV 2 | Norway | reference | 45 % | 42 | 4 / 38 | 38 | 1 | 100 % | 0 % | 100 % | 0.0 % | 0 % | 3,453 | OK |
| Nettavisen | Norway | reference | 29 % | 65 | 15 / 50 | 50 | 4 | 94 % | 6 % | 100 % | 0.0 % | 0 % | 2,564 | OK |
| SVT | Sweden | reference | 42 % | 136 | 17 / 119 | 68 | 6 | 99 % | 1 % | 100 % | 0.0 % | 0 % | 1,584 | OK |
| Aftonbladet | Sweden | reference | 46 % | 162 | 15 / 147 | 147 | 5 | 89 % | 11 % | 100 % | 6.2 % | 0 % | 716 | OK |
| Dagens Nyheter | Sweden | reference | 17 % | 177 | 20 / 157 | 153 | 5 | 21 % | 79 % | 100 % | 0.0 % | 0 % | 2,878 | OK |
| Svenska Dagbladet | Sweden | extra | 11 % | 98 | 17 / 81 | 81 | 5 | 93 % | 7 % | 100 % | 0.0 % | 0 % | 659 | OK |
| Expressen | Sweden | reference | 34 % | 79 | 20 / 59 | 59 | 5 | 65 % | 35 % | 100 % | 1.3 % | 2 % | 1,406 | OK |
| TV4 Nyheterna | Sweden | reference | 26 % | 90 | 9 / 81 | 62 | 3 | 99 % | 1 % | 100 % | 0.0 % | 0 % | 2,627 | OK |
| DR | Denmark | reference | 47 % | 45 | 17 / 28 | 28 | 5 | 100 % | 0 % | 100 % | 0.0 % | 0 % | 818 | OK |
| TV 2 Denmark | Denmark | reference | 45 % | 78 | 18 / 60 | 45 | 5 | 99 % | 1 % | 100 % | 0.0 % | 0 % | 3,119 | OK |
| Politiken | Denmark | reference | 10 % | 73 | 20 / 53 | 53 | 5 | 88 % | 12 % | 100 % | 1.4 % | 0 % | 1,058 | OK |
| Berlingske | Denmark | extra | 9 % | 61 | 9 / 52 | 52 | 3 | 97 % | 3 % | 100 % | 0.0 % | 0 % | 1,759 | OK |
| Jyllands-Posten | Denmark | extra | 9 % | 53 | 11 / 42 | 41 | 3 | 75 % | 25 % | 100 % | 0.0 % | 0 % | 1,029 | OK |
| Ekstra Bladet | Denmark | reference | 27 % | 47 | 17 / 30 | 30 | 5 | 98 % | 2 % | 100 % | 0.0 % | 2 % | 1,524 | OK |
| B.T. | Denmark | reference | 23 % | 43 | 13 / 30 | 30 | 4 | 100 % | 0 % | 100 % | 0.0 % | 0 % | 1,813 | OK |
| Yle | Finland | reference | 43 % | 52 | 19 / 33 | 31 | 6 | 100 % | 0 % | 100 % | 0.0 % | 0 % | 2,352 | OK |
| Helsingin Sanomat | Finland | reference | 27 % | 175 | 16 / 159 | 159 | 4 | 90 % | 10 % | 100 % | 0.0 % | 0 % | 1,722 | OK |
| Ilta-Sanomat | Finland | reference | 58 % | 232 | 12 / 220 | 220 | 3 | 94 % | 6 % | 100 % | 0.0 % | 0 % | 1,840 | OK |
| Iltalehti | Finland | reference | 53 % | 75 | 20 / 55 | 53 | 5 | 93 % | 7 % | 100 % | 0.0 % | 0 % | 1,597 | OK |
| MTV Uutiset | Finland | reference | 28 % | 53 | 8 / 45 | 45 | 2 | 98 % | 2 % | 100 % | 0.0 % | 0 % | 1,205 | OK |
| The New York Times | United States | reference | 13 % | 57 | 14 / 43 | 43 | 4 | 25 % | 75 % | 100 % | 0.0 % | 0 % | 5,719 | OK |
| Fox News | United States | reference | 17 % | 121 | 22 / 99 | 99 | 7 | 100 % | 0 % | 100 % | 0.0 % | 1 % | 3,026 | OK |
| NPR | United States | extra | 8 % | 42 | 19 / 23 | 23 | 5 | 100 % | 0 % | 100 % | 0.0 % | 2 % | 4,461 | OK |
| Breitbart | United States | extra | – | 101 | 16 / 85 | 85 | 5 | 97 % | 3 % | 100 % | 0.0 % | 0 % | 2,388 | OK |
| The Washington Post | United States | extra | 8 % | 80 | 20 / 60 | 60 | 5 | 22 % | 78 % | 100 % | 0.0 % | 0 % | 4,014 | OK |
| New York Post | United States | extra | – | 98 | 20 / 78 | 78 | 5 | 98 % | 2 % | 100 % | 1.0 % | 0 % | 2,475 | OK |
| Politico | United States | reference | 9 % | 46 | 19 / 27 | 5 | 5 | 91 % | 9 % | 100 % | 2.2 % | 0 % | 1,380 | OK |
| ABC News | United States | reference | 9 % | 47 | 19 / 28 | 24 | 5 | 66 % | 34 % | 100 % | 2.1 % | 0 % | 3,582 | OK |
| NBC News | United States | reference | 8 % | 47 | 15 / 32 | 28 | 7 | 91 % | 9 % | 100 % | 0.0 % | 0 % | 4,020 | OK |
| CBS News | United States | extra | 8 % | 85 | 20 / 65 | 65 | 5 | 68 % | 32 % | 100 % | 7.1 % | 0 % | 3,047 | OK |
| BBC News | United Kingdom | reference | 45 % | 88 | 20 / 68 | 58 | 6 | 95 % | 5 % | 100 % | 0.0 % | 0 % | 3,391 | OK |
| The Guardian | United Kingdom | reference | 15 % | 270 | 20 / 250 | 229 | 7 | 96 % | 3 % | 100 % | 0.0 % | 0 % | 4,399 | OK |
| The Telegraph | United Kingdom | extra | 6 % | 142 | 19 / 123 | 9 | 5 | 28 % | 72 % | 100 % | 0.7 % | 0 % | 1,962 | OK |
| Daily Mail | United Kingdom | reference | 9 % | 294 | 7 / 287 | 286 | 4 | 2 % | 98 % | 100 % | 0.0 % | 0 % | 3,960 | OK |
| Sky News | United Kingdom | reference | 13 % | 36 | 18 / 18 | 15 | 5 | 50 % | 50 % | 100 % | 0.0 % | 0 % | 2,685 | OK |
| GB News | United Kingdom | reference | 7 % | 42 | 0 / 42 | 42 | 0 | 2 % | 98 % | 100 % | 0.0 % | 0 % | 1,106 | OK |
| tagesschau | Germany | reference | 20 % | 81 | 20 / 61 | 61 | 5 | 99 % | 1 % | 100 % | 0.0 % | 0 % | 3,577 | OK |
| Der Spiegel | Germany | reference | 13 % | 105 | 20 / 85 | 85 | 5 | 80 % | 20 % | 100 % | 0.0 % | 0 % | 2,177 | OK |
| Frankfurter Allgemeine | Germany | extra | 6 % | 278 | 20 / 258 | 186 | 7 | 74 % | 26 % | 100 % | 0.4 % | 0 % | 3,720 | OK |
| Süddeutsche Zeitung | Germany | extra | 7 % | 55 | 20 / 35 | 35 | 5 | 91 % | 9 % | 100 % | 0.0 % | 0 % | 2,539 | OK |
| Die Welt | Germany | extra | 11 % | 114 | 19 / 95 | 95 | 5 | 68 % | 32 % | 100 % | 1.8 % | 0 % | 2,846 | OK |
| taz | Germany | extra | – | 112 | 20 / 92 | 92 | 5 | 100 % | 0 % | 100 % | 0.0 % | 0 % | 3,985 | OK |
| t-online | Germany | reference | 17 % | 798 | 14 / 784 | 619 | 5 | 59 % | 41 % | 100 % | 0.3 % | 0 % | 2,255 | OK |
| n-tv | Germany | reference | 14 % | 116 | 19 / 97 | 97 | 5 | 100 % | 0 % | 100 % | 0.0 % | 0 % | 3,008 | OK |
| Bild | Germany | reference | 14 % | 149 | 10 / 139 | 139 | 5 | 93 % | 5 % | 100 % | 0.0 % | 1 % | 2,109 | OK |
| Le Monde | France | reference | 12 % | 72 | 12 / 60 | 60 | 3 | 17 % | 83 % | 100 % | 0.0 % | 0 % | 2,946 | OK |
| Le Figaro | France | extra | 8 % | 86 | 20 / 66 | 63 | 6 | 0 % | 100 % | 100 % | 0.0 % | 0 % | 0 | OK |
| Libération | France | extra | – | 112 | 20 / 92 | 92 | 5 | 18 % | 82 % | 100 % | 0.0 % | 0 % | 5,538 | OK |
| France 24 | France | extra | – | 51 | 4 / 47 | 47 | 1 | 0 % | 100 % | 100 % | 2.0 % | 0 % | 0 | OK |
| Le Parisien | France | extra | 9 % | 0 | 0 / 0 | 0 | 0 | 0 % | 0 % | 0 % | 0.0 % | 0 % | 0 | CHECK: <5 in 72 h, text 0 %, dates 0 % |
| 20 Minutes | France | reference | 14 % | 72 | 20 / 52 | 50 | 5 | 96 % | 4 % | 100 % | 0.0 % | 0 % | 2,644 | OK |
| TF1 Info | France | reference | 12 % | 146 | 0 / 146 | 145 | 0 | 0 % | 100 % | 100 % | 4.1 % | 0 % | 0 | OK |
| franceinfo | France | reference | 11 % | 80 | 20 / 60 | 56 | 5 | 0 % | 100 % | 100 % | 0.0 % | 0 % | 0 | OK |
| BFMTV | France | reference | 9 % | 96 | 19 / 77 | 77 | 5 | 72 % | 25 % | 100 % | 0.0 % | 0 % | 1,881 | OK |
| El País | Spain | reference | 15 % | 266 | 19 / 247 | 226 | 6 | 45 % | 55 % | 100 % | 0.0 % | 0 % | 5,325 | OK |
| El Mundo | Spain | reference | 12 % | 85 | 15 / 70 | 70 | 4 | 100 % | 0 % | 100 % | 0.0 % | 0 % | 4,197 | OK |
| ABC | Spain | extra | 7 % | 113 | 16 / 97 | 97 | 5 | 100 % | 0 % | 100 % | 0.0 % | 0 % | 4,476 | OK |
| La Vanguardia | Spain | extra | 7 % | 314 | 14 / 300 | 280 | 4 | 99 % | 1 % | 100 % | 0.0 % | 0 % | 3,906 | OK |
| elDiario.es | Spain | reference | 14 % | 195 | 19 / 176 | 155 | 5 | 98 % | 2 % | 100 % | 0.0 % | 0 % | 5,261 | OK |
| 20minutos | Spain | reference | 12 % | 273 | 16 / 257 | 230 | 8 | 100 % | 0 % | 100 % | 0.0 % | 0 % | 2,538 | OK |
| OKDiario | Spain | reference | 12 % | 126 | 8 / 118 | 118 | 2 | 100 % | 0 % | 100 % | 0.0 % | 0 % | 3,475 | OK |
| la Repubblica | Italy | reference | 11 % | 93 | 8 / 85 | 85 | 3 | 99 % | 1 % | 100 % | 0.0 % | 0 % | 400 | OK |
| ANSA | Italy | reference | 17 % | 85 | 16 / 69 | 69 | 4 | 89 % | 11 % | 100 % | 1.2 % | 1 % | 2,790 | OK |
| Il Fatto Quotidiano | Italy | reference | 9 % | 76 | 16 / 60 | 60 | 4 | 100 % | 0 % | 100 % | 0.0 % | 1 % | 2,166 | OK |
| La Stampa | Italy | extra | – | 157 | 14 / 143 | 124 | 5 | 99 % | 1 % | 100 % | 0.0 % | 0 % | 571 | OK |
| Il Sole 24 Ore | Italy | extra | 7 % | 35 | 16 / 19 | 19 | 4 | 100 % | 0 % | 100 % | 0.0 % | 0 % | 2,986 | OK |
| Libero Quotidiano | Italy | extra | – | 132 | 14 / 118 | 118 | 4 | 86 % | 14 % | 100 % | 3.0 % | 0 % | 1,478 | OK |
| TgCom24 | Italy | reference | 17 % | 69 | 16 / 53 | 53 | 4 | 91 % | 9 % | 100 % | 0.0 % | 0 % | 1,297 | OK |
| RaiNews | Italy | reference | 11 % | 96 | 13 / 83 | 83 | 4 | 76 % | 24 % | 100 % | 0.0 % | 1 % | 1,498 | OK |
