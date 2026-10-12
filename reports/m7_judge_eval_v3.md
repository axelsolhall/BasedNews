# 3.2 judge reliability — gpt-5.4-mini, prompt v3

60 story pairs × 4 asks + 5 self pairs + 4 same-text pairs = 249 calls · $0.4575 ($0.00184/call) · 1196 tokens in / 209 out per call · story run `2026-10-11T12:51:45Z_average_0.6`

| check | econ | social | target |
|---|---|---|---|
| decisive (A or B) | 0.38 (n=60) | 0.63 (n=60) |  |
| no angle (na) | 0.62 (n=60) | 0.37 (n=60) |  |
| swap consistency | 0.67 (n=21) | 0.68 (n=31) | ≥ 0.80 |
| retest agreement | 0.80 (n=60) | 0.78 (n=60) | ≥ 0.80 |
| mirror agreement (A28) | 0.67 (n=18) | 0.82 (n=22) | ≥ 0.80 |
| picks the first-shown (A04) | 0.33 (n=48) | 0.36 (n=75) | 0.40–0.60 |
| picks the longer text (A41) | 0.54 (n=48) | 0.60 (n=75) | 0.40–0.60 |
| null pairs → same/na | – | – | ≥ 0.80 |
| identical pair → na | 1.00 (n=9) | 1.00 (n=9) |  |
| identical pair → picks the first-shown | – | – | 0.30–0.70 |

**Verdict:** some targets missed — read before measuring.
