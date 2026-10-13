# pipeline/

One subpackage per module, each with `run` and `eval` entry points (PLAN.md §2). Contracts between modules are in
`contracts.py`; storage is behind `store.py` (SQLite, locally or downloaded from S3 by the job runner; A18, A55);
settings and prices are in `settings.py` (read from `.env`). Stage numbers match the top-level README. In production
every module runs through the job runner (`job/`), which adds a record and a gate per step (docs/ARCHITECTURE.md).

| Step | Module | Package | Status | Check in isolation |
|---|---|---|---|---|
| 1.1 Sources | M1 | `sources/` | ✅ real data | `pipeline.sources.eval` |
| 1.2 Ingest | M2 | `ingest/` | ✅ real data | `pipeline.ingest.report` (`--readme`), `pipeline.ingest.eval` |
| 1.3 Backfill | M2b | `backfill/` | ✅ real data, about half fetched | `pipeline.backfill.eval` |
| 2.1 Normalize | M3 | `normalize/` | ✅ real data; vectors cached by text (A56) | `pipeline.normalize.eval --samples 3` |
| 2.2 Embed | M4 | `embed/` | ✅ OpenAI `text-embedding-3-small@512` (ADR-011); MiniLM as the local baseline | `pipeline.embed.eval`, `pipeline.embed.calibrate` |
| 2.3 Stories | M5 | `events/` | ✅ real data, threshold 0.56 (E7b); distances built in blocks (≈ 8 bytes × n²) | `pipeline.events.eval`, `pipeline.experiments.e7b analyse` |
| 3.1 Plan | M6 | `plan/` | ✅ article-aware (`plan_joint_hier`) | `tests/test_hierarchical.py`, `pipeline.sim.eval_loop` |
| 3.2 Judge | M7 | `judge/` | ✅ OpenAI, prompt v4; full call log (A51) | `pipeline.judge.eval --pairs 60`, `pipeline.judge.calllog` |
| 3.3 Rate | M8 | `rate/` | ✅ hierarchical (`hierarchical.py`); flat model kept as baseline (`model.py`) | `pytest`, `pipeline.sim.eval_articles` |
| 3 loop | — | `measure/` | ✅ budget-capped; judges new pairs as they appear (A58) | `pipeline.measure.run --dry-run`, `tests/test_settled.py` |
| 4.1 Aggregate | M9 | `aggregate/` | ✅ outlets and countries on the plane | `pipeline.aggregate.preview` |
| 4.2 Export | — | `export/` | ✅ current fit → `plane-latest.json` (A54: publishing is a decision) | `pipeline.export.run --check` |
| 5.1 Jobs | — | `job/` | ✅ runner: a record and a gate per step, exit codes | `pipeline.job history`, `tests/test_job.py` |
| 5.2 State | — | `job/storage.py` | ✅ SQLite-in-S3: lock + conditional upload (A55); tested on a fake bucket and a fake S3 client | `tests/test_storage.py` |
| A52 Retention | — | `retention/` | ✅ dry run by default; Parquet lake before any delete | `pipeline.retention.run`, `tests/test_retention.py` |

Also:
- `sim/`: simulators for E1–E3 and E5.
- `experiments/`: analyses of the real-data experiments (E4, E4b, E6, E7, E7b).
- `signoff.py`: stage scorecards.
- `version.py`: the git commit every record and export carries (the image sets it at build time).
- `net.py`: shared HTTP: one honest User-Agent, timeouts, robots.txt and TDM opt-out checks (A10).

Run any of them with `uv run python -m pipeline.<package>.<entry>`, or through the job runner:
`uv run python -m pipeline.job <step>`. The local MiniLM baseline needs `uv sync --group ml` and
`BASEDNEWS_EMBED_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`.

```bash
uv run python -m pytest -q                       # unit tests
uv run python -m pipeline.sim.eval_loop          # P1 simulator eval → reports/p1_rating_core.md + .png
uv run python -m pipeline.sim.eval_articles      # E5 article effects → reports/e5_article_effects.md
```
