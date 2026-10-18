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

---

## F3 — Most judge calls cannot answer the economic question, and a story's vector says in advance which can

**Question (2026-10-18).** The planner (M6) weighs each candidate pair by the chance that the judge answers each axis
(A32). That chance was one average per axis. Is it predictable per pair, and does using it buy more information for
the same calls?

**Observation (real data).** On live's gpt-5.4-mini calls (prompts v3 and v4), the judge found an economic angle in
**14 %** of pairs and a social one in **50 %** — a crime story or a football result has no economic framing to compare.
Every other economic call costs the same and adds nothing on that axis.

**Model.** One L2 logistic regression per axis on the mean of the pair's two article vectors (`pipeline/plan/angle.py`,
A70), trained on the judge's own answered / "no angle" verdicts; its prediction replaces the average as the pair's
relevance in the planner. Below 300 verdicts there is no model (the average is kept).

**Evidence 1 — real verdicts, out of fold** (2,126 live calls, OpenAI@512 vectors, 5 folds by story so a story is
never in train and test; `python -m pipeline.plan.angle eval --version v3,v4`):

| axis | answered overall | AUC | answered among the top-scored 10 % | 20 % | 30 % | 50 % |
|---|---:|---:|---:|---:|---:|---:|
| economic | 14 % | **0.90** | 70 % | 52 % | 41 % | 27 % |
| social | 50 % | 0.81 | 90 % | 87 % | 82 % | 73 % |

Choosing among the top-scored fifth gives ≈ 3.5× the economic answers per call.

**Evidence 2 — simulation at a fixed budget** (March 2019's real stories, 37 days, MiniLM vectors; the simulated judge
at live's estimates, whose chance of an answer depends on the story along a hidden direction calibrated to live: 14 % /
50 % answered, a perfect predictor's AUC 0.90 / 0.82; 160 calls a day plus the weekly floor, A69; outlet order
compared with the simulated truth, 124 outlets):

| | one average per axis | per-pair angle (A70) |
|---|---:|---:|
| calls | 5,985 | 5,985 |
| calls answered, economic / social | 10 % / 52 % | **35 %** / 63 % |
| economic outlet order vs truth (Spearman ρ) | 0.57 | **0.80** |
| economic outlet sd (median) | 0.72 | **0.51** |
| social ρ / sd | 0.71 / 0.35 | 0.68 / 0.32 |

The model switched itself on after four days (1,185 verdicts) and was relearned weekly from the replay's own verdicts.

**Limitations.** One simulation seed and one month; the simulated angle is linear in the vector by construction, so
the real AUC on the history's local vectors must be measured (`eval` on the history's verdicts after the pilot).
Choosing pairs by predicted angle changes which stories carry the economic verdicts; they come only from stories with
an economic angle anyway, but whether positions shift is checked on real verdicts, not assumed. Not a claim about any
outlet (pipeline phase).

**Method lesson.** What a call can tell is part of its value: weighing pairs by their own chance of an answer, learned
from the judge's past answers, is the cheapest precision available — no new calls, no new model on the GPU.

---

## F4 — Off-the-shelf local judges are cautious where the paid judge decides: they abstain on the social axis

**Question (E9, pre-registered `docs/experiments/e9_prereg.json`).** The eleven-year history needs ≈ 12 M same-story
pairs; at gpt-5.4-mini's $0.00155 a call, judging them all would cost ≈ $18,600, and a planner-picked 5 % ≈ $930. Can
an open model on a 16 GB consumer GPU (RTX 5080) answer like gpt-5.4-mini instead, for free?

**Test.** 943 gpt-5.4-mini calls with prompt v4 (693 live, 250 eval), re-asked with the same texts, order, question
direction and JSON schema. The bar is gpt-5.4-mini's agreement with itself on 60 pairs asked twice (J2). Harness 2
(no token cap; no whitespace loops in the grammar), served by vLLM 0.31.

| candidate | econ agreement (J1) | social agreement (J1) | "same/na" econ · social | output tokens | outlet order ρ econ · social (J3) |
|---|---|---|---|---:|---|
| gpt-5.4-mini vs itself (J2, the bar) | 0.92 [0.85, 0.98] | 0.83 [0.73, 0.92] | 0.77 · 0.52 | | |
| C1 Qwen3-14B-AWQ, direct | 0.81 [0.79, 0.84] | 0.58 [0.55, 0.61] | 0.81 · 0.81 | 14 | 0.17 · 0.05 |
| C2 Qwen3-14B-AWQ, thinking | 0.83 [0.81, 0.86] | 0.59 [0.56, 0.62] | 0.87 · 0.86 | 718 | 0.46 · 0.14 |
| C3 Qwen3-8B-AWQ, direct | 0.64 [0.61, 0.68] | 0.54 [0.51, 0.57] | 0.54 · 0.54 | 14 | 0.11 · 0.17 |

None reaches the bar on the social axis; the pre-registered rule hands the choice to the maintainer (D1).

**Where the gap is.** The 14B models say "no angle" on the social axis in 81–86 % of pairs where gpt-5.4-mini takes a
side in about half (52 % abstain). Reasoning first (C2) costs 50× the output tokens and changes almost nothing; the
8B model abstains less but agrees less. Outlet positions computed from the local verdicts barely follow gpt-5.4-mini's
(ρ ≤ 0.46).

**Speed (replay-shaped calls, the same server).** ≈ 580 tokens in, 14 out: 46,000 calls an hour at 16 in flight,
51,000 at 64 — the whole 5 % history in ≈ 12 GPU hours. A first speed test that copied gpt-5.4-mini's call shape (174
output tokens — its hidden reasoning — and no shared prompt prefix) had measured 10,000 an hour: measure the call
the system will actually make.

**Method lessons.**
1. A gap like this is behavioural (a calibration of when to abstain), not a lack of capability: the model that
   abstains less (C3) is not better. That points to teaching the decision boundary (E11, LoRA on gpt-5.4-mini's own
   verdicts) rather than a bigger model or more thinking.
2. The paid judge's self-agreement is the honest ceiling; it is reachable on the economic axis (0.92) only because
   most pairs are "no angle" for both.
