# E4c — why NYT and Fox land where they do (2026-10-11, $0.059)

Principle: respect the stochastic process; a wrong-looking result is acceptable if the process is thought out,
unbiased and systematic. So: no prompt tuning to move specific outlets; first ask whether a position is distinguishable
from noise, then look for causes that are independent of the outcome.

## Signal or noise? (free, v4 US/UK verdicts)
- Fox News −0.24 ± 0.53 (21 decisive verdicts): P(more conservative than the mean Lean-Left US outlet) = 0.56 → noise.
- NYT +0.64 ± 0.62 (23): P = 0.97 → a real signal in our data, diagnosed below.

## Reasons (prompt v4 + "give one short reason", same pair/order/question; reasoned = original 20/23 NYT, 6/10 Fox)
1. Relative framing on one dominant story: NYT's neutral context ("convicted of the 1995 murder of a classmate") vs
   partners leading with "botched execution" — NYT is the less sympathetic framing, so relatively more conservative.
   Legitimate under the method. Fox's news piece on the same story (her injuries) read more liberal than CBS's.
2. Labels: "Iran-backed militia" (NYT) vs "Yemen's Houthis" (BBC) → more security-framed. Legitimate.
3. Judge error: a reported quote is read as the outlet's stance ("Trump Says It Should 'Get a New President'" →
   "hostile to Ukraine"). v4's "quoting is not a leaning" does not fully prevent it.

## Two method findings (outcome-independent)
- **Article reuse:** NYT's 23 verdicts come from 9 articles (one in 10); Fox's 21 from 8 (one in 9). The model treats
  verdicts as independent, so one article's coverage dominates and the sd is too small. → hierarchical Bradley–Terry
  with article effects (A49).
- **Construct:** we measure framing of the same event, news vs news (A43). AllSides rates the whole outlet, including
  opinion and story selection. Agreement with AllSides therefore has a ceiling below 1; Fox's lean is largely selection
  and opinion, which this method does not capture yet (A50).
