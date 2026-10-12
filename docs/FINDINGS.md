# Key findings (method)

Findings about **how** to measure, established with simulations and real data. They are not claims about where any
outlet stands (pipeline phase — see README "Possible claims").

---

## F1 — An outlet must be measured over its articles: one article compared many times is not independent evidence

**Question.** Pairwise LLM verdicts are pooled into one position per outlet. When the same article appears in many
comparisons (a big story covered by many outlets), are those verdicts independent evidence about the outlet?

**Observation (real data, E4c, 2026-10-11).** The New York Times' 23 decisive social verdicts came from only 9 articles;
one article (on the Christa Pike execution) was in 10 of them. Fox News: 21 verdicts from 8 articles, one in 9. With a
flat Bradley–Terry model the NYT landed on the conservative side with P = 0.97 — essentially the character of that
one article, counted ten times.

**Model.** Each article sits around its outlet: θ_article = θ_outlet + ε, ε ~ N(0, σ_a²), θ_outlet ~ N(0, τ²). Repeated
comparisons pin down the article; what they say about the outlet is capped at one article's worth, because ε never
averages out within the article. The spreads τ and σ_a are estimated (Laplace evidence) and averaged over a grid, not
set by hand. ICC = τ² / (τ² + σ_a²) is the share of article-to-article variation explained by the outlet.

**Evidence 1 — simulation (E5, `reports/e5_article_effects.md`).** Worlds built like the real data (heavy-tailed story
sizes so articles are reused, judge order bias β = −0.9), 30 worlds per setting; nominal coverage of a 90 % interval is
0.90.

| article spread | comparisons (uses per article, mean / max) | flat model | hierarchical |
|---|---|---:|---:|
| 0.5 | 400 (1.9 / 7) | 0.83 | 0.84 |
| 0.5 | 1,500 (4.4 / 16) | 0.82 | 0.86 |
| 0.5 | 3,000 (5.9 / 18) | **0.79** | **0.85** |
| none | 400 – 3,000 | 0.88 – 0.89 | 0.89 – 0.90 |

The flat model's overconfidence **grows with article reuse**; the hierarchical model holds and costs nothing when there
is no article effect.

**Evidence 2 — real verdicts (E4: 738 decisive social verdicts, 538 articles; E4b: 205 verdicts, 158 articles).**

| | E4 (prompt v3, 10 countries) | E4b (prompt v4, US/UK) |
|---|---|---|
| ICC (outlet share of variation) | **0.13** | **0.19** |
| article spread σ_a / outlet spread τ | 1.63 / 0.64 | 1.78 / 0.87 |
| median outlet sd, flat → hierarchical | ± 0.49 → ± 0.57 | ± 0.46 → ± 0.64 |
| split-half agreement, flat → hierarchical (mean of 30 random splits) | 0.43 → **0.50** | 0.44 → **0.50** |
| NYT position, flat → hierarchical | +1.10 → **+0.49** | +0.64 → **+0.43** |

**Result.** About 81–87 % of the variation in how an LLM judges an article's framing is article-to-article within the
same outlet. Treating repeated comparisons as independent overstates certainty and lets single articles decide an
outlet's position; the hierarchical model gives honest intervals, more reproducible rankings and shrinks
single-article outliers. Planning number: locating an outlet to about ±0.2 needs on the order of (σ_a / 0.2)² ≈ 70
distinct judged articles — the binding resource is **distinct articles per outlet**, not comparisons.

**Consequence for the planner.** Selection now works per article: the expected reduction in outlet uncertainty from a
comparison is computed in the space (outlets, judge bias, articles), so the 9th look at an article is worth ≈ ¼ of a
first look at a new one (test: 0.016 vs 0.055) without any hand-set rule.

**Limitations.** (1) With few repeats per article the Laplace evidence underestimates σ_a (simulation: 0.22–0.40 for a
true 0.5), so the hierarchical model still slightly under-covers (≈ 0.85). (2) σ_a absorbs both genuine article-level
framing differences and article-specific judge noise; the two are not separated here. (3) Real-data numbers rest on one
week of verdicts.

**Reproduce.** `uv run python -m pipeline.sim.eval_articles` (E5); `uv run python -m pytest tests/test_hierarchical.py`;
model `pipeline/rate/hierarchical.py`; planner `pipeline/plan/planner.py::plan_joint_hier`; decision ADR-009; A49.

---

## F2 — An evaluation set chosen by one model decides the comparison; 512 dimensions match 1536

**Question (E7, E7b, pre-registered).** For clustering articles into stories (2.3): how many dimensions of
`text-embedding-3-small` are needed, and is it better than the local MiniLM the pipeline was built on?

**Evidence.**
- **Size (E7, 200 labelled pairs):** 512 vs 1536 recall difference +0.015 [−0.015, +0.044], AUC 0.852 vs 0.855. The
  API's 512-d output equals the 1536 vector truncated and re-normalised (mean cosine 0.99999); quality is flat down to
  256 dimensions. A 30-day window takes 98 MB at 512 vs 295 MB at 1536.
- **Model, on a set chosen by MiniLM (E7):** at MiniLM's precision, recall 0.42 (OpenAI) vs 0.83 (MiniLM). Every pair
  had been sampled from MiniLM's similarity band or its clusters.
- **Model, on a set neither chose (E7b):** 40 random anchors, the top 4 of each model pooled, 270 pairs labelled blind.
  OpenAI is ahead on every measure: AUC 0.965 vs 0.925 (difference +0.04 [+0.002, +0.10]), precision@1 0.43 vs 0.35,
  clustering F1 0.71 vs 0.60.
- **Size again, on the unbiased set (E7b addendum):** 1536 vs 512 AUC 0.964 vs 0.963, difference +0.001
  [−0.003, +0.008]; clustering F1 0.73 vs 0.70 with overlapping intervals.

**Method lessons.**
1. A labelled set built with model A (its band, its clusters) cannot rank A against B; it can even reverse the
   ranking. Comparisons need pooled, blind evaluation with equal depth, or random sampling.
2. A similarity threshold does not transfer between embedding models; re-choose the operating point on the fair set.
3. Shortened OpenAI embeddings are free to test: buy the full vector once and truncate locally.
