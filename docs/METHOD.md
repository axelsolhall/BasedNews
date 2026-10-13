# Method — measuring media bias with LLM judges at minimum cost

> **Exploratory.** E1–E3 below are simulation runs used to design the pipeline. They are **not claims**: numbers
> change as data and code change. Real-data experiments (E4–E6) are in [experiments/](experiments/); the article-effects
> finding (F1) is in [FINDINGS.md](FINDINGS.md). Hypotheses to test for real are listed in the README.
>
> §2–§3 describe the first (flat) model and planner. The current model and planner measure outlets over their articles:
> see "Article effects" at the end and ADR-009.

## 1. Problem
We want each news outlet's position on two axes — economic left–right and social liberal–conservative — **relative
to every other outlet**, plus each country's position relative to every other country, **with honest uncertainty**,
while spending as little as possible on LLM calls.

There is no ground truth and no agreed "centre". So we never ask an LLM *"how biased is X?"*. We only ask it to
**compare two articles about the same event**: *"which framing is further to the right?"* Pairwise comparisons are
easier and more consistent for both humans and LLMs than absolute scores, and they make the result relative by
construction.

## 2. Model (M8) — Bayesian Bradley–Terry with a judge-bias term
For outlets *i, j* with latent positions θ, and a judge that sees article *i* first:

    P(first judged further right) = σ( a · (θ_i − θ_j) + β )

- **θ_o ~ N(0, 1)** prior — only identifies the scale weakly; positions are reported **relative to the average outlet**.
- **β** — the judge's first-position bias. LLM judges prefer one slot; instead of paying 2× to ask both orders,
  we randomise order and **estimate β** (simulation: true 0.30 → recovered 0.29–0.31).
- **a** — evidence sharpness per comparison (1.0 for full text, < 1 for headline + summary).

Fit: MAP by Newton's method; uncertainty by the **Laplace approximation** (inverse Hessian). Every outlet gets a mean
and a posterior standard deviation; a country is a weighted mean of its reference outlets, and its standard deviation
uses the **full posterior covariance** (outlets compared mostly to each other are correlated).

Units: a gap of 1.0 means the judge picks the further-right side 73 % of the time (≈174 Elo points).

## 3. Active selection (M6) — buy the comparison that removes the most uncertainty per token
After observing a comparison with design vector *x* and predicted *p = σ(xᵀμ)*, the Laplace covariance updates in closed
form (rank-1):

    Σ' = Σ − w (Σx)(Σx)ᵀ / (1 + w xᵀΣx),     w = p(1 − p)

So the variance of any quantity of interest *vᵀθ* (an outlet, or a country average) drops by

    Δ = w (vᵀΣx)² / (1 + w xᵀΣx)

The planner scores every candidate pair (only articles about the **same event** — otherwise topic choice is confounded
with framing) by the summed Δ over all outlets and countries **still above the target uncertainty**, divided by the
comparison's **token cost**, and picks a batch greedily, applying fantasised rank-1 updates between picks. It stops when
everything is below target or the budget is spent.

Because Fisher information scales with *a²*, a cheap summary comparison beats an expensive full-text one per token iff

    a_summary > √(cost_summary / cost_full) ≈ √(540 / 2700) ≈ 0.45

The planner does not need to know this rule — it falls out of the scoring — but it gives a sharp prediction to test.

## 4. Experiments (simulation)
Synthetic world: 8 countries × 5 outlets, judge with position bias, per-article noise (deliberately violating the
"one position per outlet" assumption), realistic text availability (some outlets summary-only).

**E1 — active vs random, equal number of calls** (`pipeline.sim.eval_loop`, 5 seeds, 3,000 calls):
variance-targeted selection reached max outlet sd 0.4 with **33 % fewer calls** and max country sd 0.25 with
**25 % fewer**; 90 % credible intervals covered the truth **89–91 %** of the time; rank correlation 0.98.

**E2 — information per token, equal token budget** (`pipeline.sim.eval_cost`, 5 seeds, 2 M tokens):

| at 500 k tokens | Spearman | worst outlet sd |
|---|---|---|
| Elo, random pairs (industry baseline) | 0.66 | — (no uncertainty) |
| Bradley–Terry, random | 0.70 | 0.79 |
| BT + cost-blind active selection | 0.73 | 0.78 |
| **BT + cost-aware active selection** | **0.87** | **0.51** |

Cost-aware selection brought every outlet under sd 0.4 at 0.93 M tokens; no other method did within 2 M (> 2×).
Sensitivity: at a_summary = 0.8 it needed **68 % fewer tokens** than cost-blind selection; at 0.4 — *below* the
break-even — it switched to buying 11–21 % full-text comparisons, i.e. it adapts rather than always picking cheap.

**Honest negative result:** with random pairs, Elo ranks about as well as Bradley–Terry (Spearman 0.92 vs 0.92 at 2 M
tokens). The value of the Bayesian model is not better raw ranking; it is **calibrated uncertainty**, which is what makes
targeted, cost-aware selection possible at all.

**E3 — semi-real replay** (`pipeline.sim.eval_semireal`, 5 seeds, 750 k tokens): real event clusters (5,357 comparable
outlet pairs, 4,347 cross-country, 71 outlets in 10 countries; run 2026-10-11T10:34:36Z, after M3 normalize, A34 v2; costs = rendered judge input incl. headline history, A36), real text
availability and token costs from actual article lengths; one judge call answers both axes (A31) but the judge **abstains
on an axis the story has no angle on** (A32: ~30 % of stories economic); only the verdicts come from simulated N(0,1) positions.

| tokens | strategy | Spearman econ | Spearman social | max country sd |
|---|---|---|---|---|
| 375 k | cost-aware | 0.71 ± 0.05 | 0.75 ± 0.06 | 0.316 ± 0.004 |
| 750 k | random | 0.64 ± 0.04 | 0.70 ± 0.04 | 0.373 ± 0.015 |
| 750 k | cost-aware | **0.80 ± 0.04** | **0.82 ± 0.05** | **0.279 ± 0.008** |

Every country below sd 0.3: cost-aware ≈ 487 k tokens; random not within 750 k. This crossing point is sensitive to the
data (408 k–509 k across today's runs), so we quote it as ≈ 0.5 M.
Earlier runs on 2026-10-11 reported −41 % (52 outlets; France then had 1 of 5 reference outlets because an ingest crashed)
−25 % (before live-blog texts were removed) and −23 % (A34 v1). The number above is the current one; across
these runs the equal-spend reduction stayed between −23 % and −25 % once every country had its five outlets.

Findings only the real structure (and per-axis abstention) reveals:
1. **Uncertainty floors:** some outlets share few events with others; money cannot buy that down, only more days of data.
2. **Thinly covered outlets set the worst country:** Denmark and Norway are now least certain because three Nordic
   reference outlets (Ekstra Bladet, B.T., Nettavisen) joined the live ingest last and have few articles yet.
3. **The planner balances the axes by itself:** economic evidence is scarcer, so random selection leaves ovals ~13 % wider
   than tall (median, seed 0); cost-aware selection buys economic stories until they are as tall as they are wide (ratio 1.00). No rule for this was coded.
4. **Seeds matter:** before abstentions were modelled, a single seed suggested random ranked outlets better at full budget;
   the 5-seed mean reversed it. Every number here is a multi-seed mean.

## 5. Limitations (and how each is being tested)
| Limitation | Test |
|---|---|
| Real-data results are exploratory (one week of verdicts) | pre-registered repeats on fresh stories (E6 → next week) |
| a_summary = 0.6 is assumed | M7: same pairs with full text vs summaries (A30) |
| LLM may be asymmetric (left framing read as "neutral") | ask "more right?" and "more left?" — gap measures asymmetry (A28) |
| One global scale across countries/languages | cross-country comparisons of the same international event; stability when adding countries (A07) |
| Laplace intervals are symmetric; extremes may be skewed | coverage of the outermost outlets on real data; MCMC check (A27) |
| Event matching threshold set on 200 labelled pairs, chosen with MiniLM | done 2026-10-12: OpenAI threshold 0.56 from a blind pooled test (E7b, ADR-011); more anchors would narrow the intervals |


## Article effects (A49, ADR-009)
An outlet is not one fixed point that every article reproduces; each article sits around its outlet:

    θ_article = θ_outlet + ε_article,   ε ~ N(0, σ_a²),   θ_outlet ~ N(0, τ²)
    P(first article judged further +) = σ(a · (θ_A − θ_B + β))

**Why repeats have diminishing returns.** Write one comparison as a noisy reading of the article with variance σ_c².
After n readings of the same article, the article is known to σ_c²/n, but the outlet only to σ_a² + σ_c²/n: the
article's own deviation ε never averages out within that article. The information about the outlet from one article,
1/(σ_a² + σ_c²/n), rises with n toward a ceiling of 1/σ_a², so the n-th reading adds less and less (σ_a = 0.5,
σ_c = 1: the 10th look is worth 0.11 of the first). Only new articles push past the ceiling.

**Estimating the spreads.** τ and σ_a are not fixed by hand: each grid point (τ, σ_a) is fitted by Newton (MAP) and
scored by its Laplace marginal likelihood; the posterior averages over the grid with evidence weights, so uncertainty
about the spreads reaches the outlet intervals. The intra-class correlation ICC = τ² / (τ² + σ_a²) is the share of
article-to-article variation that is "which outlet" rather than "which article".

**Planning.** The planner works in the space (outlets, β, articles): a not-yet-judged article enters with variance σ_a²
and no correlation, a well-judged article is pinned down; the expected reduction in outlet/country variance per token
(rank-1 Laplace update) therefore favours new articles without any extra rule.
