# ADR-004 — Event clustering: average linkage over threshold-connected components
**Industry standard / prototype:** cosine threshold + connected components (union-find).
**Explored:** time-aware similarity (72 h window, decay) + average-linkage agglomerative clustering.
**Evidence (real data, 2,460 articles):** connected components chained a 403-article blob; average linkage's largest
cluster was 31, with 206 multi-outlet and 111 cross-country events across 6+ languages. Threshold awaits human labels.
**Decision:** average linkage.

## Measured 2026-10-11 (labels by Claude)
On 100 similarity-stratified pairs: average linkage 0.60 → precision 0.92, recall 0.74; 0.55 → 0.85 / 0.87;
components 0.60 → precision 0.61. On 100 deployed comparable pairs: 88 % same event, 98 % same story. Threshold stays
0.60 (precision first). Details: VERIFY.md, 2.3.
After cleaning HTML out of feed summaries (A38): 0.60 → 0.92 / 0.76; 0.55 → 0.89 / 0.89. Kept 0.60 pending more labels.
