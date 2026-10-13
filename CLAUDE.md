# BasedNews — project instructions for agents

Read first: `PLAN.md` (product), `docs/ARCHITECTURE.md` (how it runs in production), `ASSUMPTIONS.md`, `BACKLOG.md`.
Global rules in `~/.claude/CLAUDE.md` apply (pipeline modules, every module evaluable, assumptions recorded).

Project specifics:
- Pipeline code lives in `pipeline/` as one subpackage per module (`pipeline/<module>/`), each with
  `run` and `eval` entry points: `uv run python -m pipeline.<module>.run` / `.eval`.
- Contracts between modules are defined once in `pipeline/contracts.py`; change them deliberately and note it.
- Only build BACKLOG items marked `executable: yes`. Capture new requests in BACKLOG before ending a session.
- New assumption → add to `ASSUMPTIONS.md` with the next free ID; reference it in code as `# Axx`.
- Anything the maintainer must confirm by hand → `VERIFY.md`.
- Never publish article full text: the site and its JSON carry aggregates only (A10). Never commit `.env` or API keys.
- LLM spend must always go through the budget guard (A48), and every paid call is logged in full (A51).
- Production runs every module through the job runner (`uv run python -m pipeline.job <step>`); a new module needs a
  `Step` in `pipeline/job/steps.py` and a gate in `pipeline/job/gates.py` (A53).
- Infrastructure is Terraform in `infra/`. Never run `terraform apply`, create AWS resources or switch schedules on:
  the maintainer applies by hand (A57). `terraform plan`, `validate` and checkov are fine.
- Embedding calls cost money too: they go through the same monthly cap (A48) and the vector cache (A56).
- A model comparison needs a test set neither model chose (finding F2: pooled, blind, equal depth).
