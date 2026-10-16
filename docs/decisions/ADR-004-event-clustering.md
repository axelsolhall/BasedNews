# ADR-004 — Event clustering: average linkage, cut at the threshold
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

## Computing it at 41 countries (2026-10-16)
The method is unchanged; only how it is computed. A full 7-day window of 41 countries is ≈ 170 k articles (≈ 24 k a
day), and the close pairs chain about half of them into one linked group, so any method that holds every pairwise
distance of that group runs out of memory: 2.7 GB on 2026-10-15, 7.3 GB on 2026-10-16 (failed runs, 07:05–17:05),
≈ 60 GB once the window is full. `average_sparse` keeps only the pairs at distance ≤ t plus the running sum of distances
between candidate stories (Lance–Williams), computing a missing sum from the vectors, and merges the closest candidate
until none is ≤ t. That is exactly the tree's cut (average linkage is reducible). Checked on the real window of
2026-10-15 (41,677 articles): the identical partition as the previous exact method, 12 s and 0.8 GB instead of 53 s and
2.7 GB; the 2026-10-16 window (60,851 articles): the whole step in 31 s at 1.3 GB.
