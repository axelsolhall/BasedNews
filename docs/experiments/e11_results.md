# E11 — a local judge trained on gpt-5.4-mini's verdicts (LoRA): results so far (2026-10-18)

Pre-registration `docs/experiments/e11_prereg.json` (10:26 UTC, before any call or training run). Not a claim
(pipeline phase): these numbers say whether the local judge is fit for the history, not where any outlet stands.

## What was done
- **Data (batch b1):** 3,000 history pairs on 250 days (2 a month, 2015-11 → 2026-09, seeded), 173 outlets, judged by
  gpt-5.4-mini with prompt v4 for **$3.58**; plus E10's 668 fresh US/UK verdicts. One day in ten held out for
  validation (324 pairs; 322 fit the 1,024-token limit). Nothing from October 2026, so E9's 943 test calls are unseen.
- **Training:** QLoRA on the 4-bit Qwen3-14B (rank 16, all projections, 2 epochs, loss on the answer only) on the RTX
  5080: 386 steps, 123 min. Served on the AWQ base by vLLM as a LoRA module. Settings as pre-registered, unchanged.
- **Learning curve:** the same training on the first third and two thirds of the training days (nested, by date).

## Results on E9's 943 test calls (95 % bootstrap intervals; reports/e9.md)

| judge | training examples | agreement econ | agreement social | κ econ · social | outlet order ρ econ · social |
|---|---:|---|---|---|---|
| gpt-5.4-mini vs itself (the bar, J2) | | 0.92 [0.85, 0.98] | 0.83 [0.73, 0.92] | | |
| Qwen3-14B untrained (C1) | 0 | 0.82 [0.79, 0.84] | 0.58 [0.55, 0.61] | 0.48 · 0.20 | 0.17 · 0.05 |
| C4, first third | 1,312 | 0.84 [0.81, 0.86] | 0.65 [0.62, 0.68] | 0.53 · 0.38 | 0.12 · 0.29 |
| C4, two thirds | 2,198 | 0.84 [0.81, 0.86] | 0.64 [0.61, 0.67] | 0.54 · 0.34 | 0.34 · 0.39 |
| C4, all | 3,085 | 0.84 [0.82, 0.87] | 0.67 [0.64, 0.70] | 0.57 · 0.40 | 0.38 · 0.39 |

![learning curve](../../reports/e11_learning_curve.png)
![training](../../reports/e11_training.png)

- **Training doubles social κ (0.20 → 0.40) for $3.58**, and outlet order from the local verdicts starts to follow
  gpt-5.4-mini's (social ρ 0.05 → 0.39). Econ moves little: it was already near its "no angle" floor.
- **Validity (E9's rule): not valid** — the social interval [0.64, 0.70] does not reach the bar's [0.73, 0.92].
  The maintainer decides (D1). The working view (2026-10-18): matching the paid judge is not the goal; separating
  outlets on the plane is, and that is tested on the pilot's positions, not on agreement.
- **Learning curve:** most of the gain comes with the first third; after it, agreement moves within its noise
  (0.65 → 0.64 → 0.67), while validation loss keeps falling (0.0703 → 0.0681 → 0.0652): the probabilities improve
  where the chosen answer does not change.
- **The pre-registered rule on the curve:** social rose 2.5 points from two thirds to all (> 2), so **another batch
  is to be proposed** (b2, ≈ $3.60, its own go). Read with the curve: the first third scored above two thirds, so the
  rise is within the noise of 943 calls (± 3 points); the rule fires on noise as easily as on a trend.
- **Where the gap is:** C4 misses (abstains where gpt-5.4-mini decides) on 24 % of social calls and flips (the other
  side) on 7 %; the gap to the bar is mostly abstention.

## E12 — reading the probabilities, with a cut for "na" (pipeline/experiments/e12.py, A71)
The question: are C4's misses decisions it nearly made? If so, answering whenever P(na) is below a cut turns misses
into agreement for free. Method fixed before the test calls: the cut per axis tuned on the validation days for
the highest agreement (E9's J1), then E9's 943 calls asked once as candidate C4-T.

**On the validation days (reports/e12_cut.png):** agreement is flat across cuts — 0.895 → 0.898 econ, 0.784 →
0.796 social at the best cuts (econ 0.12, social 0.25). Answering more often turns misses into inventions and flips
almost one for one: from the greedy answer to a cut of 0.7 on social, misses fall 6.8 points while inventions rise
5.0 and flips 3.4, and agreement drops (0.784 → 0.769). So **the misses are not hidden decisions**: where C4 abstains, its A-vs-B lean is close to a coin. The
greedy answer is already about where the probabilities put the line. The cut chosen for agreement abstains *more*,
because these days are mostly "no angle" (85 % econ, 72 % social) and agreement rewards matching that.

**On E9's test calls (C4-T, reports/e12.md, reports/e9.md):** the frozen cuts do worse than the greedy answer — social
agreement 0.64 [0.61, 0.67] vs 0.67, κ 0.31 vs 0.40; econ κ 0.49 vs 0.57. They abstain more (misses 31 % vs 24 % on
social), as on the validation days. Outlet order moves within its noise (ρ econ 0.42 vs 0.38, social 0.34 vs 0.39).

**What the probabilities show (reports/e12_cut.png).** Econ is cleanly two-humped: P(na) near 0 where gpt-5.4-mini
decides, near 1 where it does not. Social is not: where gpt-5.4-mini decides, C4's P(na) is spread over the whole
range — the model cannot tell those pairs from no-angle ones, so no cut can recover them.

**Two side findings.**
- **The best cut depends on the pairs, not the judge.** On the test calls (52 % no-angle, chosen by the planner) the
  social curve peaks near a cut of 0.7; on the validation days (72 % no-angle, random pairs) near 0.25. A cut tuned on
  one mix of pairs does not carry to another — and the history's pairs are planner-picked with the angle model (A70),
  a third mix.
- **The local judge is not perfectly repeatable.** The likeliest option read from the probabilities matched C4's own
  greedy run on 96.5 % (econ) and 93.8 % (social) of the same calls; mismatches are near-ties (median margin 0.04 on
  econ) — numerics that differ with the server's batching. About 2 points of agreement are run-to-run noise.

## What it means for the method
1. **Agreement with the teacher is the wrong yardstick for a threshold.** It counts a miss and an invention alike
   and rewards abstaining on a set that is mostly "no angle". What the history needs is information about outlet
   positions per GPU hour: a miss costs one cheap local call, an invention or a flip adds noise to the positions.
2. **A local call is cheap, so a stricter judge can be the better one.** At the validation cut for social, C4
   decides less (14 % vs 21 %) but flips less often when both decide (18 % vs 24 %) and invents less (1.9 % vs 5.2 %).
   Whether purer, fewer verdicts separate outlets better is a question for the pilot's positions, not for agreement.
3. **More of the same data buys little** beyond the first third; the next lever, if any, is how the model is taught
   (weighting the decided examples) — or none, if the pilot already separates outlets.
