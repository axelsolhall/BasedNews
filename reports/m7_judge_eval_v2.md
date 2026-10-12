# 3.2 judge reliability — gpt-5.4-mini, prompt v2

60 story pairs × 4 asks + 5 self pairs + 5 same-text pairs = 250 calls · $0.5190 ($0.00208/call) · 1305 tokens in / 244 out per call · story run `2026-10-11T12:51:45Z_average_0.6`

| check | econ | social | target |
|---|---|---|---|
| decisive (A or B) | 0.22 (n=60) | 0.30 (n=60) |  |
| no angle (na) | 0.73 (n=60) | 0.65 (n=60) |  |
| swap consistency | 0.80 (n=10) | 0.80 (n=10) | ≥ 0.80 |
| retest agreement | 0.82 (n=60) | 0.68 (n=60) | ≥ 0.80 |
| mirror agreement (A28) | 1.00 (n=6) | 0.91 (n=11) | ≥ 0.80 |
| picks the first-shown (A04) | 0.32 (n=25) | 0.34 (n=32) | 0.40–0.60 |
| picks the longer text (A41) | 0.32 (n=25) | 0.59 (n=32) | 0.40–0.60 |
| null pairs → same/na | 1.00 (n=10) | 1.00 (n=10) | ≥ 0.80 |

**Verdict:** some targets missed — read before measuring.
