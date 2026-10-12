# ADR-003 — LLM position bias: estimate it instead of paying 2×
**Industry standard:** ask every pair in both orders and average.
**Explored:** randomise presentation order, model the bias as one parameter β per axis.
**Evidence:** simulation recovers β (0.30 → 0.29–0.31) with no extra calls. To be confirmed on the real judge (M7 swap test).
**Decision:** estimate β; "ask both orders" remains a config fallback.
