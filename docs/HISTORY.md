# The history, step by step (A61, A65–A67)

From the fetched pages to trajectories since 2015-11. Everything is local and free; the gaming PC does the GPU work
(`docs/LOCAL_LLM.md`). Each step resumes where it stopped. `PC=<user>@<pc>` below is the gaming PC over SSH.

| # | Step | Command | Done when |
|---|---|---|---|
| 0 | Pages fetched (Mac) | `PYTHONPATH=. uv run python -m pipeline.history.prefetch` | `data/history/prefetch.log` reaches the current month |
| 1 | Local judge chosen (E9) | `uv run python -m pipeline.experiments.e9 run --candidate C1` (C2, then C3 after switching the model), `… analyse` | `reports/e9.md`; the maintainer accepts (D1); `HISTORY_JUDGE` in `pipeline/history/replay.py` set to it |
| 2 | Local embedding model chosen (E8) | `… e8 export`, `PC=… sh scripts/e8_on_pc.sh`, `… e8 sample`, blind labels, `… e8 analyse` | `reports/e8.json`; D1; the model and its threshold noted |
| 3 | History vectors | `PYTHONPATH=. uv run python -m pipeline.history.vectors export --model <hf id> --out data/history/vec`; copy `texts.jsonl` to the PC; `embed_texts.py … --dtype float16` (judge paused); copy `vectors.npy` back; `… vectors import --model <name> --dir data/history/vec` | `vectors import` reports every article |
| 4 | Count pairs (dry run) | `PYTHONPATH=. uv run python -m pipeline.history.replay --prefetched --embed-model <name> --threshold <E8's> --vectors data/history/vec --count-only`, then `… replay summary --fraction 0.05` | pairs per outlet and quarter; GPU hours for 5 % |
| 5 | Pilot judging | one year first: `… replay --prefetched --embed-model <name> --threshold <E8's> --vectors data/history/vec --judge local --judge-fraction 0.05 --start 2019-01-01 --end 2019-12-31` (or `--calls-per-day N`, or `--target-sd s`: A68) | `history_progress` covers the year; then the rest |
| 6 | Trajectories | `PYTHONPATH=. uv run python -m pipeline.history.trajectories` | `data/history/trajectories.json`; per-outlet sd per quarter → the full fraction (D2) |

**Where the replay runs.** The Mac holds the database today (≈ 7 GB, 49 GB free). At 1,024 dimensions the vectors are
≈ 13 GB as float16; the PC has 161 GB free and the GPU next to it. Running steps 3–6 on the PC (copy the database once)
avoids moving vectors twice — the maintainer's call.

**What each step costs.** Step 3: about an hour of GPU for ≈ 6 M short texts. Step 4: CPU only. Step 5: calls ÷
the judge's rate: ≈ 51,000 calls an hour (A65, measured 2026-10-18 with replay-shaped calls; the 10,000 of 2026-10-17
used the wrong call shape) — a year at 5 % ≈ 1 hour of GPU, the whole history ≈ 12. Each day also costs CPU time
(grouping, the fit, the planner) while the GPU waits; at this speed that may dominate (measure it in the pilot). Step 6: minutes (A67: 205 outlets, weekly bins, sparse).

**The budget (A68).** The planner picks every call; the budget only says how many. Simulated on March 2019 with a
judge as informative as live's, 5 % of new pairs (≈ 160 a day) recovers the outlet order only weakly in three weeks
(ρ ≈ 0.45); it takes ≈ 3,000 a day for ρ ≈ 0.8 within a week. Calls carry little (headlines; no economic angle in
≈ 6 of 7 pairs), so the lever is information per call — per-story relevance (BACKLOG) — not fewer calls. The pilot
therefore runs one year first. However well the planner knows an outlet, it still gets at least `--min-per-week`
comparisons (default 5) in any 7 days (A69), so no outlet is ever cemented.

**Checks.** `uv run pytest -q tests/test_replay.py tests/test_history_vectors.py tests/test_dynamic.py
tests/test_trajectories.py` (the pieces on synthetic data); `uv run python -m pipeline.history.llm_check` (the
server); `reports/e8.json`, `reports/e9.md` (the models).

## First dry run (2026-10-18, March 2019, before the model choice)
Step 4 on a copy of one month (2019-02-23 → 03-31), with the local baseline MiniLM at its E7 threshold 0.60 — not the
history's model, so the counts will shift with E8's winner. 132 of 205 outlets had pages that month.

| | |
|---|---|
| stories in the 7-day window | ≈ 6,400, of which ≈ 1,750 from more than one outlet |
| new judgeable pairs a day | ≈ 3,150 on average in March (1,865 – 5,231); 104,363 over the 37 days |
| at 5 % | ≈ 160 calls a day; the median outlet gets ≈ 90 verdicts in the 37 days (≈ 220 a quarter) |
| over 2015-11 → 2026-10, if months are alike | ≈ 11–12 M new pairs; 5 % ≈ 0.6 M calls ≈ 12 GPU hours at 51 k an hour (A65) |
| time | ≈ 4 minutes for the 37 days on the Mac (embedding included) |

The budget counts each pair once, on the day its later article appeared: a pair stays in the pool for its 7-day
window, so a budget on the whole pool would have judged ≈ 30 % of pairs at "5 % a day".
