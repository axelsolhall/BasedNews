"""A64: every step attempt writes a performance document (time, CPU, peak memory vs limit, cost), while it runs and
at the end; a killed attempt leaves its last in-progress document; `pipeline.job perf` reports them."""
import datetime as dt
import json
import time

import numpy as np

from pipeline.job import perf, storage
from tests.test_storage import state, step  # noqa: F401  (fixture)


def test_meter_measures_cpu_memory_and_writes_while_running():
    docs = []
    m = perf.Meter("r", "s", write=docs.append, sample_s=0.02, write_s=0.05,
                   limits={"vcpu": 0.5, "memory_mb": 4096}).start()
    a = np.ones(150 * 2**20 // 8)                          # ≈ 150 MB, touched
    t = time.monotonic()
    while time.monotonic() - t < 0.3:
        a.sum()
    d = m.finish("ok")
    assert any(x["status"] == "running" for x in docs) and docs[-1]["status"] == "ok"
    assert d["cpu_s"] > 0.1 and d["peak_rss_mb"] >= 150 and 0 < d["peak_share"] < 1
    assert d["cost_usd"] > 0 and d["phases"]["run_s"] >= 0.3


def test_a_job_writes_perf_to_the_bucket_with_its_phases(state, monkeypatch):  # noqa: F811
    step(monkeypatch, "s_ok", "ok")
    assert storage.run_with_state(f"file://{state}", "s_ok", "r1") == 0
    files = list((state / "runs" / "r1" / "perf").glob("s_ok.*.json"))
    assert len(files) == 1
    d = json.loads(files[0].read_text())
    assert d["status"] == "ok"
    assert {"lock_wait_s", "download_s", "db_mb", "run_s", "upload_s", "total_s"} <= set(d["phases"])
    rec = json.loads((state / "runs" / "r1" / "s_ok.json").read_text())
    assert rec["perf"]["phases"]["upload_s"] == d["phases"]["upload_s"]          # the record carries the same numbers
    assert perf.writer is perf.local_writer and perf.task_phases == {}           # reset for the next job


def test_a_failed_step_still_writes_perf(state, monkeypatch):  # noqa: F811
    step(monkeypatch, "s_boom", "boom")
    storage.run_with_state(f"file://{state}", "s_boom", "r2")
    (f,) = (state / "runs" / "r2" / "perf").glob("s_boom.*.json")
    assert json.loads(f.read_text())["status"] == "failed"


def test_report_marks_a_silent_running_attempt_as_killed_and_projects_cost():
    now = dt.datetime(2026, 10, 17, 3, 0, tzinfo=dt.UTC)
    base = {"task": {"vcpu": 0.5, "memory_mb": 4096}, "cpu_share": 0.9, "rss_samples": []}
    docs = [
        {**base, "run_id": "a", "step": "stories", "attempt": "2026-10-17T02:40:00Z", "updated_at": "2026-10-17T02:48:00Z",
         "status": "running", "phases": {"run_s": 480, "total_s": 500}, "peak_rss_mb": 3900, "peak_share": 0.95,
         "cost_usd": 0.004},
        {**base, "run_id": "a", "step": "stories", "attempt": "2026-10-17T02:50:00Z", "updated_at": "2026-10-17T02:59:30Z",
         "status": "ok", "phases": {"run_s": 300, "total_s": 330, "download_s": 9, "upload_s": 12, "db_mb": 1100},
         "peak_rss_mb": 1500, "peak_share": 0.37, "cost_usd": 0.003},
    ]
    out = perf.report(docs, now=now)
    assert "killed?" in out and "3900 / 4096 (95%)" in out and "process" in out
    assert "a month" in out
    assert perf.report(docs, job="refresh", now=now) == "no performance documents in that period"
