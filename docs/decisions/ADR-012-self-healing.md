# ADR-012 — Self-healing operations: a failure fixes itself or emails once

**Status:** accepted 2026-10-15; automatic deploy rollback removed the same day (the maintainer: self-healing
belongs in the jobs, not in deploys) · **Amends:** ADR-010 (single writer, alarms), A55 · **Records:** A62

## Context
In the first 36 hours of production the maintainer had to step in three times, each time for the same reason: a job
that died left `db/LOCK` behind, and every later job waited for it for up to 3 hours.

| When | What died | How |
|---|---|---|
| 2026-10-14 16:06 | ingest | lxml crashed under six threads (exit 133) |
| 2026-10-15 04:09 | stories | out of memory on the old clustering (exit 137) |
| 2026-10-15 05:11 | a stopped refresh | its container lived on for 80 s and took the lock just after it was deleted, then was killed |

A deploy also failed silently: a lint error kept CI from shipping the memory fix, and production went on with the old
image until it ran out of memory. The maintainer's requirement: it must heal itself; a person fixing it is a failure.

## Decision
1. **The lock is a lease** (`pipeline/job/storage.py::Lease`).
   - The holder renews it every 60 s from a background thread.
   - A lease not renewed for 5 min belongs to a dead job and is taken over (conditional write on its ETag, so two
     jobs cannot both win).
   - A holder renews for at most 80 min, so a hung job cannot keep it; the state machine stops a task at 90 min.
   - A lease is released only by its owner.
   - Before uploading, a job checks the lease is still its own (fencing). The database upload stays conditional on the
     version downloaded, as before.
2. **A stop is clean.** SIGTERM (ECS StopTask, a timeout) raises `Stopped` in the step. The record is written, nothing
   is uploaded and the lease is released. The task gets 120 s before SIGKILL (`stopTimeout`).
3. **A failed step is retried once**, after 2 min, by the state machine. Thanks to the lease the retry can take over a
   crashed attempt's lock. The paid step is not retried: a retry would start a second daily budget (A48).
4. **A broken image never becomes `prod`.** CI runs `python -m pipeline.job selftest` inside the built arm64 image
   before pushing it. It checks:
   - every step's imports;
   - the clustering against the full tree;
   - text extraction in 8 threads;
   - SQLite and Arrow;
   - the lease.
5. **Email means "it did not heal".**
   - The hourly refresh alarms on *no success for 3 hours*, not on single failures, and sends an all-clear when it
     recovers.
   - Daily jobs alarm on a failed execution, which already includes its retry.

## Standard vs explored
| | Common practice | Here |
|---|---|---|
| single writer | a database with transactions, or DynamoDB locks with TTL | a lease object in S3 with conditional writes (no extra service, ≈ free) |
| bad deploys | blue/green or canary, health-checked | a smoke test of the image in CI before it ships; no automatic rollback |
| paging | alert on every failure | alert on lack of success after self-repair |

## Consequences
- **Recovery times:**
  - a crashed, killed or stopped job costs at most one run and about 5–7 minutes;
  - a broken image is caught in CI by the smoke test; one that gets through fails runs until it is fixed (alarm);
  - a hung job is cut off at 80–90 minutes.
- **What is left for a person:**
  - a failure that persists after the retry and takeover, e.g. a bad image, a source outage or a data bug. The 3-hour alarm says so, with a
    runbook (docs/DEPLOY.md);
- **Cost:** a retry run now and then: cents.
- **Tested:**
  - `tests/test_lease.py`: crash, OOM, stop, hung holder, lost lease, takeover of an old-format lock;
  - `pipeline/job/selftest.py` in CI.
- **Not covered:**
  - an AWS region outage;
  - the database file in S3 being deleted. Versioning keeps 24 versions to restore from; that is manual on purpose.
