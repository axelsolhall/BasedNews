# E13 — tuning the LoRA judge: results as they come (2026-10-18)

Pre-registration `docs/experiments/e13_prereg.json` (amended before any C5/C6 step: first third of the days).
Research behind it: `docs/experiments/e13_research.md`. Not a claim (pipeline phase).

## C4-S — C4 read in both orders, probabilities averaged
| | econ κ | social κ | social agreement | outlet order ρ econ · social |
|---|---:|---:|---|---|
| C4 (one order) | 0.57 | 0.40 | 0.67 [0.64, 0.70] | 0.38 · 0.39 |
| C4-S (both orders) | 0.59 | 0.34 | 0.64 [0.61, 0.67] | 0.33 · 0.49 |

- **Not better by the rule** (social κ must rise ≥ 0.03). C4 stays.
- **Order consistency of C4:** read one order at a time, its answer is the same in both orders on 90.7 % (econ) and
  86.2 % (social) of calls; both orders decide but on opposite sides on 3.8 % and 5.4 %.
- **Why agreement cannot reward it:** gpt-5.4-mini answered each test pair in one order. If it has a position bias,
  a student that copies the bias agrees more and one that cancels it agrees less. Removing order noise can only show
  on positions (social ρ rose 0.39 → 0.49, inside its interval) — the pilot's question.

## What the misses are (30 of C4's 222 social misses on E9, drawn at random, read one by one)
A miss = gpt-5.4-mini took a social side, C4 said "no angle".
- **≈ 11 of 30: stories with no social angle**, where gpt-5.4-mini still chose — a tiger attack at a wildlife park
  (4 pairs), the Pope's lung surgery (2), an airport strike, a migrant boat, a mass shooting (2), a botched execution.
  Prompt v4 tells the judge to pick "if one article leans even slightly more", so the teacher's side here is mostly
  noise; C4 abstaining is arguably the better measurement.
- **≈ 14 of 30: political stories with subtle differences** — Trump and Zelensky over the diesel deal (8), the Justice
  Department and the TV pool (4), others; framing differences are slight and often about foreign policy, where the
  social axis is a stretch. Genuinely ambiguous; gpt-5.4-mini agrees with itself on social only 83 % of the time.
- **≈ 5 of 30: a clear cue C4 missed** — "loyal" vs "conservative commentator" for a press secretary; a French
  deputy's boycott of a cancer campaign; two different races presented as one story.
- So most misses are not capability a better prompt would unlock; a few are.

## What the test set is (and is not)
E9's 943 calls are **one day of live judging (2026-10-11)**, 239 stories with the 10 largest giving 32 % of calls,
56 % with full article text. The history judges **headline + description over eleven years**. So E9 is narrow
(calls on one story are correlated, so its intervals are too narrow) and shifted from the history's input. E11's
validation days (324 pairs on 27 days, 2016-02 → 2026-01, headline + description) match the history better, though
they are small and picked each run's checkpoint.

## The Qwen3-14B four (2026-10-19) — each change alone against C4-1/3
Same first third of the training days, same 164 steps, same test (E9's 943 calls). Rule: social κ ≥ C4-1/3's + 0.03 = 0.41.

| | econ κ | social κ | social agreement [95 %] | social miss · invent | econ miss · invent | outlet order ρ econ · social |
|---|---:|---:|---|---|---|---|
| C1 untrained | 0.48 | 0.21 | 0.58 [0.55, 0.61] | 33.4 % · 4.5 % | 8.1 % · 3.6 % | 0.17 · 0.05 |
| C4-1/3 LoRA | 0.53 | 0.38 | 0.65 [0.62, 0.68] | 22.7 % · 3.8 % | 7.5 % · 2.3 % | 0.12 · 0.29 |
| C5 LoRA, swap-augmented | 0.46 | 0.35 | 0.64 [0.61, 0.67] | 25.7 % · 3.0 % | 9.8 % · 1.5 % | 0.05 · 0.28 |
| C7 LoRA, balanced weights | 0.60 | 0.42 | 0.66 [0.63, 0.69] | 18.0 % · 5.8 % | 3.3 % · 5.1 % | 0.14 · 0.24 |

- **C7 passes the rule, narrowly** (0.415 vs 0.409): the weights trade misses for inventions, as intended — social
  misses 22.7 → 18.0 %, inventions 3.8 → 5.8 %; on econ misses fall 7.5 → 3.3 %. Its econ κ rises most (0.53 → 0.60).
- **C5 (swap) is worse on both axes**; on econ, when both decide, it takes the wrong side more often (49 % vs 42 %).
- **The validation days disagree:** C7's checkpoint (step 120, lowest unweighted validation loss) agrees less with
  gpt-5.4-mini there (econ 0.85, social 0.76) than C4-1/3's (0.89, 0.78); it abstains less (social "na" 72 % vs
  76 %) on days where gpt-5.4-mini mostly says "na". Agreement rewards abstaining when "na" dominates, and the
  validation days have no κ, so this is not a contradiction of the test — but the test is one live day and the
  validation days look like the history (“What the test set is”, above). The pilot's positions are the check that matters.

## The Gemma 4 12B four (2026-10-19, prereg amended_3, A73) — same recipes, same test
| | econ κ | social κ | agreement econ · social [95 %] | social miss · invent | outlet order ρ econ · social [95 %] |
|---|---:|---:|---|---|---|
| G1 untrained | 0.60 | 0.41 | 0.84 · 0.64 [0.61, 0.67] | 15.1 % · 11.7 % | 0.06 · 0.50 [0.27, 0.58] |
| **G4-1/3 LoRA** | **0.63** | **0.49** | **0.87 · 0.70 [0.67, 0.73]** | 16.9 % · 4.7 % | **0.46 [0.16, 0.61] · 0.63 [0.36, 0.65]** |
| G5 LoRA, swap-augmented | 0.64 | 0.41 | 0.87 · 0.68 [0.65, 0.70] | 23.6 % · 2.7 % | 0.54 · 0.61 |
| G7 LoRA, balanced weights | 0.64 | 0.41 | 0.86 · 0.66 [0.63, 0.69] | 18.7 % · 7.0 % | 0.52 · 0.55 |
| *best Qwen: C7* | *0.60* | *0.42* | *0.84 · 0.66* | *18.0 % · 5.8 %* | *0.14 · 0.24* |

- **Untrained Gemma ≈ the best trained Qwen** (social κ 0.41 vs 0.42): it decides more readily than Qwen (misses 15 %
  vs 33 % untrained) but invents sides where gpt-5.4-mini says "na" (12 %).
- **The LoRA fixes the inventions** (12 → 5 %) at a small cost in misses: social κ 0.41 → 0.49, agreement 0.70.
- **Within Gemma neither change helps** (rule: ≥ 0.49 + 0.03): swap and weights both fall back to social κ 0.41.
  The weights push toward deciding, which Gemma already does; on Qwen, which abstained too often, they helped.
- **Across models, by the rule** (best Gemma ≥ best Qwen + 0.03): G4-1/3's 0.488 vs C7's 0.415 → **Gemma LoRA
  replaces Qwen** as the history's judge candidate. E9's own decision rule picks G4-1/3 too (agreement intervals
  overlap gpt-5.4-mini's retest ceiling on both axes).
- **Outlet order** — the question the history asks — moves most: the order of 71 outlets from G4-1/3's verdicts
  matches the order from gpt-5.4-mini's at ρ 0.46 (econ) and 0.63 (social), against ≤ 0.29 for every Qwen variant.
- **The validation days agree this time:** G4-1/3's checkpoint scores social 0.81 there (C4-1/3: 0.78; C7: 0.76).
- Speed: Gemma answered the 943 calls in ≈ 3 min at 16 in parallel, as Qwen did; its KV cache holds ≈ 12 k tokens
  (infra/pc/llm-gemma.env), so the history's throughput needs measuring before the 12 GPU-hour budget is relied on.

## Gemma's learning curve (2026-10-19) — G4-1/3's recipe on one third, two thirds and all of the training days
| | training examples · steps | econ κ | social κ | agreement econ · social [95 %] | outlet order ρ econ · social [95 %] | validation days econ · social |
|---|---|---:|---:|---|---|---|
| G1 untrained | 0 | 0.60 | 0.41 | 0.84 · 0.64 [0.61, 0.67] | 0.06 · 0.50 | 0.89 · 0.78 |
| **G4-1/3** | 1,310 · 164 | **0.63** | **0.49** | 0.87 · **0.70 [0.67, 0.73]** | 0.46 [0.16, 0.61] · **0.63 [0.36, 0.65]** | 0.90 · 0.81 |
| G4-2/3 | 2,197 · 276 | 0.63 | 0.46 | 0.87 · 0.70 [0.67, 0.73] | 0.39 [0.09, 0.58] · 0.58 [0.34, 0.65] | 0.91 · 0.81 |
| G4 (all) | 3,085 · 386 | 0.62 | 0.46 | 0.85 · 0.70 [0.67, 0.73] | 0.50 [0.21, 0.62] · 0.44 [0.21, 0.56] | 0.89 · 0.82 |

- **Flat: more data does not help Gemma.** Agreement and κ stay within their noise from one third to all; the point
  estimates fall slightly. Qwen's curve (E11) was flat too.
- So the second batch of gpt-5.4-mini labels (b2) is not needed for the LoRA; G4-1/3 stays the candidate.
- On the validation days social agreement sits at ≈ 0.81 for every share, close to gpt-5.4-mini's agreement with
  itself (0.83): copying its verdicts has little room left.
