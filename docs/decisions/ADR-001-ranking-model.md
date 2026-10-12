# ADR-001 — Ranking model: Bayesian Bradley–Terry instead of Elo
**Industry standard:** Elo (also the project's first prototype, `scripts/order_algo.ipynb`, with a K-factor grid search).
**Explored:** Bayesian Bradley–Terry, Laplace posterior, judge position-bias term β, per-comparison sharpness a.
**Evidence:** E2 — equal ranking quality to Elo with random pairs, but BT gives calibrated uncertainty (coverage 0.89–0.97),
which enables active selection: Spearman 0.87 vs Elo 0.66 at the same token spend.
**Decision:** BT. Elo stays as the baseline in every experiment.
