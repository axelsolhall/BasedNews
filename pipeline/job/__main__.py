"""5.1 Job runner CLI (ADR-010).

    uv run python -m pipeline.job list                        # steps and jobs
    uv run python -m pipeline.job refresh                     # a job: its steps in order, stops at the first failure
    uv run python -m pipeline.job export                      # one step
    uv run python -m pipeline.job measure --budget-usd 1      # a paid step needs an explicit budget (A48)
    uv run python -m pipeline.job history [--n 20]            # the last step records: what failed, where, why
    BASEDNEWS_STATE=s3://… uv run python -m pipeline.job push-db   # once: seed the stored database (never overwrites)

Options the runner does not know are passed to the module (one step only). With BASEDNEWS_STATE set, the database is
downloaded first and uploaded only if every step succeeded (`pipeline/job/storage.py`).
Exit codes: 0 ok · 1 step failed · 2 gate failed · 3 refused (configuration) · 4 the stored database changed meanwhile.
"""
from __future__ import annotations

import argparse
import os
import sys

from pipeline import store
from pipeline.job import runner
from pipeline.job.steps import JOBS, STEPS


def history(n: int) -> None:
    rows = store.recent_job_runs(store.connect(), n)
    if not rows:
        print("no job runs recorded yet")
        return
    for r in reversed(rows):
        why = r["error"] or ""
        if not why and r["gate"]:
            why = ", ".join(f"{k}={v}" for k, v in list((r["gate"].get("metrics") or {}).items())[:4])
        print(f"{r['started_at']}  {r['step']:<10} {r['status']:<12} {r['duration_s'] or 0:>7.1f}s  "
              f"{r['commit_sha'] or '':<20} {why[:110]}")
    print(f"\nfull record: logs/jobs/<run_id>/<step>.json, or SELECT * FROM job_runs WHERE run_id = '{rows[0]['run_id']}'")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline.job", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", help="a job, a step, 'list', 'history' or 'push-db'")
    ap.add_argument("--run-id", help="group steps of one execution (Step Functions passes its execution name)")
    ap.add_argument("--no-gate", action="store_true", help="skip the gate (debugging only; the record says so)")
    ap.add_argument("--init-state", action="store_true", help="allow starting without a stored database (first deploy)")
    ap.add_argument("--n", type=int, default=20, help="rows for 'history'")
    args, extra = ap.parse_known_args(argv)

    if args.target == "list":
        for s in STEPS.values():
            flags = [f for f, on in (("paid", s.paid), ("publishes", s.publishes), ("needs ML", s.needs_ml)) if on]
            print(f"step {s.name:<12} {s.module:<26} {' '.join(s.args):<12} {', '.join(flags)}")
        for j, steps in JOBS.items():
            print(f"job  {j:<12} {' → '.join(steps)}")
        return 0
    if args.target == "history":
        if os.environ.get("BASEDNEWS_STATE"):          # read the stored database, not the container's empty one
            import tempfile
            from pipeline.job.storage import DB_KEY, open_bucket
            path = os.path.join(tempfile.mkdtemp(prefix="basednews-"), "basednews.sqlite")
            open_bucket(os.environ["BASEDNEWS_STATE"]).get(DB_KEY, path)
            os.environ["BASEDNEWS_DB"] = path
        history(args.n)
        return 0
    if args.target == "push-db":
        if not os.environ.get("BASEDNEWS_STATE"):
            ap.error("push-db needs BASEDNEWS_STATE (s3://bucket/prefix or file:///folder)")
        from pipeline.job.storage import push_db
        return push_db(os.environ["BASEDNEWS_STATE"], store.default_db())
    if args.target not in JOBS and args.target not in STEPS:
        ap.error(f"unknown job or step '{args.target}' (try: list)")
    if extra and args.target in JOBS:
        ap.error(f"module options {extra} only apply to a single step, not the job '{args.target}'")
    state = os.environ.get("BASEDNEWS_STATE")
    if state:
        from pipeline.job.storage import run_with_state
        return run_with_state(state, args.target, args.run_id, extra, gate=not args.no_gate, init=args.init_state)
    code, _ = runner.run(args.target, args.run_id, extra, gate=not args.no_gate)
    return code


if __name__ == "__main__":
    sys.exit(main())
