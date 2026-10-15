# ADR-012 — Self-healing operations: a failure fixes itself or emails once

**Status:** accepted 2026-10-15 · **Amends:** ADR-010 (single writer, alarms), A55 · **Records:** A62

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
5. **A bad deploy rolls itself back.** CI tags the outgoing `prod` image `prev` before pushing the new one. On every
   failed execution, a Lambda (`infra/main/rollback`) checks whether the refresh has failed twice in a row with the new
   image and never succeeded with it. If so, it points `prod` back at `prev` and emails why. After a rollback
   `prod == prev`, so it cannot loop.
6. **Email means "it did not heal".**
   - The hourly refresh alarms on *no success for 3 hours*, not on single failures, and sends an all-clear when it
     recovers.
   - Daily jobs alarm on a failed execution, which already includes its retry.
   - A rollback emails once.

## Standard vs explored
| | Common practice | Here |
|---|---|---|
| single writer | a database with transactions, or DynamoDB locks with TTL | a lease object in S3 with conditional writes (no extra service, ≈ free) |
| bad deploys | blue/green or canary, health-checked | batch jobs have no traffic to shift: smoke test before, rollback on two failed runs after |
| paging | alert on every failure | alert on lack of success after self-repair |

## Consequences
- **Recovery times:**
  - a crashed, killed or stopped job costs at most one run and about 5–7 minutes;
  - a broken image is caught in CI, or rolled back within about 2 hours (two hourly runs);
  - a hung job is cut off at 80–90 minutes.
- **What is left for a person:**
  - a failure that persists after rollback, e.g. a source outage or a data bug. The 3-hour alarm says so, with a
    runbook (docs/DEPLOY.md);
  - re-deploying a rolled-back change once it is fixed.
- **Cost:** one Lambda invocation per failed run, and a retry run now and then: cents.
- **Tested:**
  - `tests/test_lease.py`: crash, OOM, stop, hung holder, lost lease, takeover of an old-format lock;
  - `tests/test_rollback.py`: two failures, one failure, an image that worked, no loop, manual stops;
  - `pipeline/job/selftest.py` in CI.
- **Not covered:**
  - an AWS region outage;
  - the database file in S3 being deleted. Versioning keeps 24 versions to restore from; that is manual on purpose.
