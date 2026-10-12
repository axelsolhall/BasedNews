# 3.2 judge reliability — gpt-5.4-mini, prompt v4

60 story pairs × 4 asks + 5 self pairs + 5 same-text pairs = 250 calls · $0.4238 ($0.00170/call) · 1138 tokens in / 187 out per call · story run `2026-10-11T14:05:31Z_average_0.6`

| check | econ | social | target |
|---|---|---|---|
| decisive (A or B) | 0.28 (n=60) | 0.62 (n=60) |  |
| no angle (na) | 0.72 (n=60) | 0.38 (n=60) |  |
| swap consistency | 0.64 (n=14) | 0.82 (n=28) | ≥ 0.80 |
| retest agreement | 0.92 (n=60) | 0.83 (n=60) | ≥ 0.80 |
| mirror agreement (A28) | 0.73 (n=15) | 0.67 (n=21) | ≥ 0.80 |
| picks the first-shown (A04) | 0.33 (n=33) | 0.39 (n=70) | 0.40–0.60 |
| picks the longer text (A41) | 0.39 (n=33) | 0.73 (n=70) | 0.40–0.60 |
| null pairs → same/na | – | – | ≥ 0.80 |
| identical pair → na | 1.00 (n=10) | 1.00 (n=10) |  |
| identical pair → picks the first-shown | – | – | 0.30–0.70 |

**Verdict:** some targets missed — read before measuring.
