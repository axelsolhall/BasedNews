# Teaching a small local judge: what research and practice say (2026-10-18)

The question behind E13: E11's LoRA judge (C4) doubled social κ for $3.58, then flattened; a cut on its "no angle"
probability did not help (E12). What else do researchers and practitioners do to make a small judge reliable, and
what does it mean for measuring outlets?

## 1. Position bias, and asking both orders
Pairwise LLM judges change their verdict when the two items swap places. MT-Bench found even the strongest judge
consistent under a swap only ~65 % of the time; a systematic study finds the bias across 15 judges and many tasks,
strongest when the two items are close ([Judging the Judges](https://arxiv.org/abs/2406.07791)). The standard fix is
to ask both orders and keep only what survives the swap (or count disagreement as a tie).
**Here:** the prompt already randomises which article is A, and the rating model estimates an order bias β, so the
bias cannot move positions on average — but it adds noise to every verdict. Reading both orders and averaging the
probabilities (C4-S) removes that noise at the price of twice the prefill, which is cheap locally.

## 2. Swap augmentation when fine-tuning a judge
JudgeLM ([Zhu et al., ICLR 2025](https://arxiv.org/abs/2310.17631)) fine-tunes 7–33B judges on GPT-4's verdicts and
reaches > 90 % agreement with the teacher. One of its three fixes is swap augmentation: every training pair also
shown with the two answers swapped and the teacher's verdict swapped with them, so the student learns content, not
position. **Here:** C5 (the same 386 steps, every example also swapped).

## 3. Rare classes, and where to spend the next labels
Work that trains small classifiers on LLM labels for stance and ideology ([RED-CT](https://arxiv.org/abs/2408.08217))
weights rare classes and spends new labels where the model is least sure (the gap between its top two label
log-probabilities); it beat the LLM labels themselves on 7 of 8 tasks. **Here:** C6 weights decided verdicts by
n_na / n_decided. If another batch is ever bought (b2), it should be drawn where C4 is least sure and from the pairs
the history planner actually picks — E12 showed the right behaviour depends on that mix.

## 4. Using a noisy judge for valid conclusions
The statistics literature treats an LLM judge as a cheap, imperfect measurement. Two lines matter here:
- **Judge-aware Bradley–Terry** ([BT-σ, ICML 2026](https://arxiv.org/abs/2602.16610)): one discrimination parameter
  per judge, learned from the comparisons themselves; ignoring judge differences gives intervals too narrow around
  biased estimates. Our model already has a per-comparison sharpness; a per-judge one would let gpt-5.4-mini's and
  the local judge's verdicts be combined, each weighted by how sharp it is.
- **Prediction-powered inference** ([prediction-powered ranking](https://arxiv.org/abs/2402.17826); also
  [noisy-judge inference](https://arxiv.org/abs/2601.05420)): many cheap judgments plus a small random set judged by
  the better judge as well; the difference on that set corrects the cheap judge's bias, and the intervals stay valid.
  This is the principled guard against the one failure volume cannot fix: a local judge that is biased, not just
  noisy.

## What it means for BasedNews
1. Tonight (E13, pre-registered): both orders (C4-S), swap-trained (C5), swap-trained with balanced weights (C6),
   and C5/C6 in both orders; the pilot's judge is chosen by E9 κ with a pre-set margin.
2. The pilot then measures what matters: do outlets separate on the plane, and how many GPU hours does it take.
3. If the local judge is noisier but unbiased, volume fixes it. To *show* it is unbiased, judge a small random share
   of the pilot's pairs with gpt-5.4-mini too (a few dollars, the maintainer's go) — the prediction-powered design.
