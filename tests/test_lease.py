"""ADR-012: the lock is a lease, so a dead job never blocks the pipeline and nobody ever deletes a lock by hand.
These are the three failures that needed manual fixes on 2026-10-14/15 — a crash (exit 133), an OOM kill (137) and a
stopped task that grabbed the lock on its way out — plus the guards around them."""
import json

import pytest

from pipeline.job import runner, storage
from pipeline.job.steps import Step
from tests.test_storage import headlines, state  # noqa: F401 — the fixture


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_a_dead_holder_is_taken_over_after_the_lease_not_after_hours(tmp_path):
    b, clock = storage.LocalBucket(tmp_path), Clock()
    dead = storage.Lease(b, "crashed", clock=clock)
    assert dead.acquire(wait_s=0, beat=False)               # it never renews again: crash / OOM kill
    nxt = storage.Lease(b, "next", clock=clock)
    assert not nxt.acquire(wait_s=0, beat=False)            # still fresh: wait
    clock.t += storage.LEASE_S + 1                          # 5 minutes of silence, not 3 hours
    assert nxt.acquire(wait_s=0, beat=False)
    assert json.loads(b.get_bytes(storage.LOCK_KEY)[0])["run_id"] == "next"


def test_a_live_holder_keeps_its_lease_by_renewing(tmp_path):
    b, clock = storage.LocalBucket(tmp_path), Clock()
    live = storage.Lease(b, "live", clock=clock)
    assert live.acquire(wait_s=0, beat=False)
    for _ in range(10):                                     # 10 × 4 min: far past one lease, renewed each time
        clock.t += 4 * 60
        assert live.renew()
        assert not storage.Lease(b, "intruder", clock=clock).acquire(wait_s=0, beat=False)
    assert live.still_ours()


def test_a_hung_holder_stops_renewing_after_the_hold_limit(tmp_path):
    b, clock = storage.LocalBucket(tmp_path), Clock()
    hung = storage.Lease(b, "hung", clock=clock)
    assert hung.acquire(wait_s=0, beat=False)
    clock.t += storage.MAX_HOLD_S + 1
    assert not hung.renew()                                 # no more heartbeats …
    clock.t += storage.LEASE_S + 1
    assert storage.Lease(b, "next", clock=clock).acquire(wait_s=0, beat=False)   # … so the next job gets in


def test_release_never_deletes_someone_elses_lease(tmp_path):
    b, clock = storage.LocalBucket(tmp_path), Clock()
    old = storage.Lease(b, "old", clock=clock)
    assert old.acquire(wait_s=0, beat=False)
    clock.t += storage.LEASE_S + 1
    new = storage.Lease(b, "new", clock=clock)
    assert new.acquire(wait_s=0, beat=False)                # took over
    assert not old.renew() and old.lost
    old.release()                                           # the old one wakes up and cleans up …
    assert json.loads(b.get_bytes(storage.LOCK_KEY)[0])["run_id"] == "new"   # … but the new lease stays


def test_an_old_format_lock_is_taken_over_too(tmp_path):
    b, clock = storage.LocalBucket(tmp_path), Clock()
    b.put_bytes(json.dumps({"run_id": "v1-job", "at": clock.t, "host": "x"}).encode(), storage.LOCK_KEY)
    clock.t += storage.LEASE_S + 1                          # locks written before the lease have no heartbeat
    assert storage.Lease(b, "next", clock=clock).acquire(wait_s=0, beat=False)


def test_the_heartbeat_thread_renews_on_its_own(tmp_path):
    b = storage.LocalBucket(tmp_path)
    lease = storage.Lease(b, "beating", heartbeat_s=0.02)
    assert lease.acquire(wait_s=0)
    first = b.get_bytes(storage.LOCK_KEY)[1]
    import time
    time.sleep(0.2)
    assert b.get_bytes(storage.LOCK_KEY)[1] != first and lease.still_ours()
    lease.release()
    with pytest.raises(KeyError):
        b.get_bytes(storage.LOCK_KEY)


def step(monkeypatch, name, arg):
    monkeypatch.setitem(runner.STEPS, name, Step(name, "tests.fake_steps", (arg,)))


def test_a_stopped_step_uploads_nothing_and_releases_the_lease(state, monkeypatch):  # noqa: F811
    step(monkeypatch, "s_stop", "stopped")
    before = (state / storage.DB_KEY).read_bytes()
    assert storage.run_with_state(f"file://{state}", "s_stop", "r-stop") == storage.EXIT_STOPPED
    assert (state / storage.DB_KEY).read_bytes() == before            # the half-done step's write was not kept
    assert not (state / storage.LOCK_KEY).exists()                     # the next job does not have to wait
    assert json.loads((state / "runs" / "r-stop" / "s_stop.json").read_text())["status"] == "failed"


def test_a_job_that_lost_its_lease_never_uploads(state, monkeypatch):  # noqa: F811
    step(monkeypatch, "s_lose", "lose_lease")
    monkeypatch.setenv("FAKE_BUCKET", f"file://{state}")
    before = (state / storage.DB_KEY).read_bytes()
    assert storage.run_with_state(f"file://{state}", "s_lose", "r-lose") == runner.EXIT_CONFLICT
    assert (state / storage.DB_KEY).read_bytes() == before
    assert json.loads((state / storage.LOCK_KEY).read_text())["run_id"] == "someone-else"   # left to its holder
