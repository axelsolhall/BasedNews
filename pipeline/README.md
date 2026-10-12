# pipeline/

One subpackage per module, each with `run` and `eval` entry points (PLAN.md §2). Contracts between modules are in
`contracts.py`; storage is behind `store.py` (SQLite now, Postgres later, A18); settings and prices are in
`settings.py` (read from `.env`). Stage numbers match the top-level README.

| Step | Module | Package | Status | Check in isolation |
|---|---|---|---|---|
| 1.1 Sources | M1 | `sources/` | ✅ real data | `pipeline.sources.eval` |
| 1.2 Ingest | M2 | `ingest/` | ✅ real data | `pipeline.ingest.report` (`--readme`), `pipeline.ingest.eval` |
| 1.3 Backfill | M2b | `backfill/` | ✅ real data, about half fetched | `pipeline.backfill.eval` |
| 2.1 Normalize | M3 | `normalize/` | ✅ real data | `pipeline.normalize.eval --samples 3` |
| 2.2 Embed | M4 | `embed/` | ✅ MiniLM; OpenAI built, not calibrated | `pipeline.embed.eval`, `pipeline.embed.calibrate` |
| 2.3 Stories | M5 | `events/` | ✅ real data | `pipeline.events.eval` |
| 3.1 Plan | M6 | `plan/` | ✅ article-aware (`plan_joint_hier`) | `tests/test_hierarchical.py`, `pipeline.sim.eval_loop` |
| 3.2 Judge | M7 | `judge/` | ✅ OpenAI, prompt v4; full call log (A51) | `pipeline.judge.eval --pairs 60`, `pipeline.judge.calllog` |
| 3.3 Rate | M8 | `rate/` | ✅ hierarchical (`hierarchical.py`); flat model kept as baseline (`model.py`) | `pytest`, `pipeline.sim.eval_articles` |
| 3 loop | — | `measure/` | ✅ budget-capped | `pipeline.measure.run --dry-run` |
| 4.1 Aggregate | M9 | `aggregate/` | 🟧 no export of the current fit to the site yet | `pipeline.aggregate.preview` |

Also:
- `sim/`: simulators for E1–E3 and E5.
- `experiments/`: analyses of the real-data experiments (E4, E4b, E6).
- `signoff.py`: stage scorecards.
- `net.py`: shared HTTP: one honest User-Agent, timeouts, robots.txt and TDM opt-out checks (A10).

Run any of them with `uv run python -m pipeline.<package>.<entry>`. Add `--group ml` for normalize, embed and events
with local MiniLM.

```bash
uv run python -m pytest -q                       # unit tests
uv run python -m pipeline.sim.eval_loop          # P1 simulator eval → reports/p1_rating_core.md + .png
uv run python -m pipeline.sim.eval_articles      # E5 article effects → reports/e5_article_effects.md
```
