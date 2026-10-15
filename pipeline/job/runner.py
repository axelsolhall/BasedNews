"""5.1 Job runner (ADR-010): run steps in order, record each one, stop at the first failure.

Every step writes one record: run id, step, status (ok | failed | gate_failed | refused), commit, arguments, table
counts before and after, the gate's numbers, the error and traceback. The record goes to the `job_runs` table and to
logs/jobs/<run_id>/<step>.json; in production the JSON is uploaded to the private bucket even when the step fails,
because the database is then not uploaded (`pipeline/job/storage.py`). Logs are JSON lines on stdout (CloudWatch);
the module's own output is wrapped line by line.
"""
from __future__ import annotations

import contextlib
import gc
import importlib
import importlib.util
import io
import json
import os
import socket
import sys
import time
import traceback
import uuid
from pathlib import Path

from pipeline import store
from pipeline.ingest.feeds import utc_now_iso
from pipeline.job.steps import JOBS, STEPS, Step
from pipeline.judge.calllog import redact
from pipeline.version import commit

EXIT_OK, EXIT_FAILED, EXIT_GATE, EXIT_REFUSED, EXIT_CONFLICT = 0, 1, 2, 3, 4
EXIT_FOR = {"ok": EXIT_OK, "failed": EXIT_FAILED, "gate_failed": EXIT_GATE, "refused": EXIT_REFUSED}
COUNTS = {
    "articles": "SELECT COUNT(*) FROM articles",
    "canonical_articles": "SELECT COUNT(*) FROM articles WHERE canonical_id IS NULL AND kind IS NULL",
    "headlines": "SELECT COUNT(*) FROM headlines",
    "embeddings": "SELECT COUNT(*) FROM embeddings",
    "event_members": "SELECT COUNT(*) FROM event_members",
    "judge_calls": "SELECT COUNT(*) FROM judge_calls",
    "judgments": "SELECT COUNT(*) FROM judgments",
    "judge_call_log": "SELECT COUNT(*) FROM judge_call_log",
    "positions": "SELECT COUNT(*) FROM positions",
    "spend_usd": "SELECT COALESCE(SUM(cost_usd), 0) FROM api_usage",
}


def new_run_id() -> str:
    return f"{utc_now_iso().replace(':', '')}-{uuid.uuid4().hex[:6]}"


def records_dir() -> Path:
    return Path(os.environ.get("BASEDNEWS_RECORDS") or "logs/jobs")


def records_of(run_id: str) -> list[dict]:
    """The step records a run has written so far (a stopped run uploads whatever exists)."""
    d = records_dir() / run_id
    return [json.loads(p.read_text()) for p in sorted(d.glob("*.json"))] if d.exists() else []


def log(event: str, stream=None, **fields) -> None:
    """One JSON line per event. CloudWatch Logs Insights can filter on any field."""
    print(json.dumps({"ts": utc_now_iso(), "event": event, **fields}, default=str, ensure_ascii=False),
          file=stream or sys.stdout, flush=True)


class _Lines(io.TextIOBase):
    """Wraps a module's prints: each complete line becomes an `output` event with the run id and step."""

    def __init__(self, stream, **fields):
        self.stream, self.fields, self.buf = stream, fields, ""

    def writable(self) -> bool:
        return True

    def write(self, s: str) -> int:
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            if line.strip():
                log("output", self.stream, line=redact(line), **self.fields)
        return len(s)

    def flush(self) -> None:
        if self.buf.strip():
            log("output", self.stream, line=redact(self.buf), **self.fields)
        self.buf = ""


def snapshot(conn) -> dict:
    out = {}
    for k, q in COUNTS.items():
        try:
            out[k] = conn.execute(q).fetchone()[0]
        except Exception:                                    # a table a future schema drops must not stop the run
            out[k] = None
    return out


def _delta(before: dict, after: dict) -> dict:
    return {k: round(after[k] - before[k], 6) for k in after
            if isinstance(after.get(k), (int, float)) and isinstance(before.get(k), (int, float)) and after[k] != before[k]}


def _budget(step: Step, args: list[str]) -> tuple[list[str], float | None, str]:
    """A48: a paid step needs an explicit budget, from --budget-usd or BASEDNEWS_RUN_BUDGET_USD. Returns (args, budget, refusal)."""
    if not step.paid or "--dry-run" in args or any(a in ("simulated", "--judge=simulated") for a in args):
        return args, None, ""
    if "--budget-usd" in args:
        return args, float(args[args.index("--budget-usd") + 1]), ""
    env = os.environ.get("BASEDNEWS_RUN_BUDGET_USD")
    if env:
        return [*args, "--budget-usd", env], float(env), ""
    return args, None, f"'{step.name}' spends AI money: pass --budget-usd or set BASEDNEWS_RUN_BUDGET_USD (A48)"


def _ml_missing(step: Step) -> str:
    if not step.needs_ml:
        return ""
    from pipeline.settings import load_settings
    if load_settings().embed_model.startswith("openai:") or importlib.util.find_spec("sentence_transformers"):
        return ""
    return (f"'{step.name}' needs the local embedding model: build the image with WITH_ML=1, or switch to OpenAI "
            f"embeddings (BASEDNEWS_EMBED_MODEL=openai:..., A45)")


def run_step(name: str, run_id: str, extra: list[str] | tuple = (), gate: bool = True) -> dict:
    step = STEPS[name]
    out = sys.stdout
    args, budget, refusal = _budget(step, [*step.args, *extra])
    refusal = refusal or _ml_missing(step)
    rec = {"run_id": run_id, "step": name, "started_at": utc_now_iso(), "commit_sha": commit(), "args": args,
           "host": socket.gethostname(), "error": None, "traceback": None, "gate": None}
    log("step_started", out, run_id=run_id, step=name, args=args, commit=rec["commit_sha"])
    conn = store.connect()
    rec["counts_before"] = snapshot(conn)
    conn.close()
    t0 = time.monotonic()
    status = "refused" if refusal else "ok"
    interrupted = None                                      # a stop (SIGTERM) or Ctrl-C: record first, then end
    if refusal:
        rec["error"] = refusal
    else:
        lines = _Lines(out, run_id=run_id, step=name)
        argv = sys.argv
        try:
            sys.argv = [step.module, *args]
            with contextlib.redirect_stdout(lines), contextlib.redirect_stderr(lines):
                importlib.import_module(step.module).main()
        except SystemExit as e:
            if e.code not in (None, 0):
                status, rec["error"] = "failed", f"module exited with code {e.code}"
        except BaseException as e:                          # noqa: BLE001 — every failure must reach the record
            status = "failed"
            rec["error"] = redact(f"{type(e).__name__}: {e}")[:2000]
            rec["traceback"] = redact(traceback.format_exc())[-20000:]
            if isinstance(e, KeyboardInterrupt):
                interrupted = e
        finally:
            sys.argv = argv
            lines.flush()
        if status != "ok":
            gc.collect()          # a crashed module's connection may still hold a write transaction: free it first
    rec["duration_s"] = round(time.monotonic() - t0, 2)
    conn = store.connect()
    rec["counts_after"] = snapshot(conn)
    if not gate and step.gate:
        rec["gate"] = {"ok": None, "reason": "skipped (--no-gate)", "metrics": {}}
    elif step.gate and status != "refused" and interrupted is None:
        ctx = {"started_at": rec["started_at"], "before": rec["counts_before"], "after": rec["counts_after"],
               "delta": _delta(rec["counts_before"], rec["counts_after"]), "budget_usd": budget}
        try:
            g = step.gate(conn, ctx)
            rec["gate"] = g.as_dict()
            if status == "ok" and not g.ok:
                status, rec["error"] = "gate_failed", g.reason
        except Exception as e:                               # a broken gate is a failure, never a silent pass
            rec["gate"] = {"ok": False, "reason": f"gate error {type(e).__name__}: {e}", "metrics": {}}
            if status == "ok":
                status, rec["error"] = "gate_failed", rec["gate"]["reason"]
                rec["traceback"] = redact(traceback.format_exc())[-20000:]
    rec["status"], rec["finished_at"] = status, utc_now_iso()
    try:
        store.save_job_run(conn, rec)
        conn.commit()
    except Exception as e:                                   # the JSON record below still exists
        log("record_not_saved", out, run_id=run_id, step=name, error=str(e))
    conn.close()
    path = records_dir() / run_id / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, indent=2, default=str))
    log("step_finished", out, run_id=run_id, step=name, status=status, duration_s=rec["duration_s"],
        delta=_delta(rec["counts_before"], rec["counts_after"]), gate=rec["gate"], error=rec["error"])
    if interrupted is not None:
        raise interrupted
    return rec


def run(target: str, run_id: str | None = None, extra: list[str] | tuple = (), gate: bool = True) -> tuple[int, list[dict]]:
    """Run a job (its steps in order) or a single step. Stops at the first step that is not ok."""
    names = JOBS.get(target) or ((target,) if target in STEPS else None)
    if names is None:
        raise KeyError(f"unknown job or step '{target}'. Jobs: {', '.join(JOBS)}. Steps: {', '.join(STEPS)}")
    run_id = run_id or new_run_id()
    recs = []
    for name in names:
        rec = run_step(name, run_id, extra if len(names) == 1 else (), gate)
        recs.append(rec)
        if rec["status"] != "ok":
            skipped = names[names.index(name) + 1:]
            if skipped:
                log("job_stopped", run_id=run_id, failed_step=name, skipped=list(skipped))
            return EXIT_FOR[rec["status"]], recs
    return EXIT_OK, recs
