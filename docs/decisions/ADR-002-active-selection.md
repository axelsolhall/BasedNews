# ADR-002 — Which comparison to pay for: variance reduction per token
**Industry standard:** random or round-robin pairing; in the active-learning literature, expected information gain (D-optimal).
**Explored:** random → information gain → variance-targeted (A-optimal on outlets *and* countries still above target)
→ **variance reduction per token** (cost-aware).
**Evidence:** E1 (−25–33 % calls), E2 (> 2× fewer tokens to target; adapts on both sides of the a² break-even).
Information gain alone under-serves country targets late in the budget (E1 plot).
**Decision:** cost-aware variance targeting; random kept as a baseline.
