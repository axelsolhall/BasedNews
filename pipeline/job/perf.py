"""5.1 Performance report (A64): every step attempt writes how long it took, its CPU and peak memory against the task's
limits and, on Fargate, what it cost — every minute while it runs and once when it ends.

    BASEDNEWS_STATE=s3://… AWS_PROFILE=basednews uv run python -m pipeline.job perf [--days 2] [--job process]
    uv run python -m pipeline.job perf                       # local runs (logs/jobs/)

Where: runs/<run_id>/perf/<step>.<attempt>.json in the state bucket (written by pipeline/job/storage.py), or next to
the step record locally (logs/jobs/<run_id>/perf/). A step killed for memory never reaches its end: its last
in-progress document (status "running", memory samples so far) remains, and the report shows it as "killed?".
Cost counts from process start to the last upload, at Fargate's on-demand price; it misses the container start
(≈ 30 s of image pull) and Fargate's 1-minute minimum, so it is a slight underestimate.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import resource
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

from pipeline.ingest.feeds import utc_now_iso

PROCESS_T0 = time.monotonic()           # imported with the job CLI: ≈ the task's start, minus the container start
SAMPLE_S = 5.0                          # memory sample interval
WRITE_S = 60.0                          # in-progress document interval (the lease heartbeat's cadence)
PRICE_VCPU_HOUR = 0.0356                # Fargate on-demand, Linux/ARM, eu-north-1: AWS Price List API, read 2026-10-16
PRICE_GB_HOUR = 0.00392


def rss_mb() -> float | None:
    """Resident memory now (Linux, /proc); None elsewhere."""
    try:
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 2**20
    except (OSError, ValueError, IndexError):
        return None


def peak_rss_mb() -> float:
    """The process's peak resident memory so far (ru_maxrss: KiB on Linux, bytes on macOS)."""
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / 2**20 if sys.platform == "darwin" else r / 1024


def cpu_s() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


def task_limits() -> dict:
    """{vcpu, memory_mb} of this Fargate task from the ECS task metadata endpoint (v4); {} outside ECS."""
    base = os.environ.get("ECS_CONTAINER_METADATA_URI_V4")
    if not base:
        return {}
    try:
        with urllib.request.urlopen(f"{base}/task", timeout=2) as r:
            lim = json.load(r).get("Limits") or {}
        return {"vcpu": float(lim["CPU"]), "memory_mb": float(lim["Memory"])}
    except Exception:                                    # no limits means no cost line, never a failed step
        return {}


def cost_usd(seconds: float, limits: dict) -> float | None:
    if not limits:
        return None
    return round(seconds / 3600 * (limits["vcpu"] * PRICE_VCPU_HOUR + limits["memory_mb"] / 1024 * PRICE_GB_HOUR), 6)


def local_writer(doc: dict) -> None:
    from pipeline.job.runner import records_dir
    path = records_dir() / doc["run_id"] / "perf" / f"{doc['step']}.{doc['attempt'].replace(':', '')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1))


writer = local_writer                   # storage.run_with_state points this at the state bucket
task_phases: dict = {}                  # set by storage.run_with_state before the step: lock wait, download, db size


class Meter:
    """One step attempt: wall and CPU time, memory sampled every `sample_s` in a thread, the document written by
    `write(doc)` every `write_s` while it runs and once at the end. Never raises into the step."""

    def __init__(self, run_id: str, step: str, write=None, sample_s: float = SAMPLE_S, write_s: float = WRITE_S,
                 limits: dict | None = None, phases: dict | None = None):
        self.run_id, self.step, self.write = run_id, step, write
        self.sample_s, self.write_s = sample_s, write_s
        self.limits = task_limits() if limits is None else limits
        self.phases = dict(phases or {})
        self.attempt = utc_now_iso()
        self.samples: list[list[float]] = []            # [seconds since start, MB]
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> Meter:
        self.t0, self.c0, self.peak0 = time.monotonic(), cpu_s(), peak_rss_mb()
        self._sample()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="perf")
        self._thread.start()
        return self

    def _sample(self) -> None:
        mb = rss_mb()
        if mb is not None:
            self.samples.append([round(time.monotonic() - self.t0, 1), round(mb, 1)])

    def _loop(self) -> None:
        last = time.monotonic()
        while not self._stop.wait(self.sample_s):
            self._sample()
            if self.write and time.monotonic() - last >= self.write_s:
                last = time.monotonic()
                self._emit(self.doc("running"))

    def _emit(self, doc: dict) -> None:
        try:
            self.write(doc)
        except Exception as e:                           # a report must never fail the step
            print(json.dumps({"ts": utc_now_iso(), "event": "perf_not_written", "step": self.step, "error": str(e)}),
                  file=sys.__stderr__, flush=True)

    def peak_mb(self) -> float:
        """This attempt's peak. One step per process (Fargate): the process peak, spikes between samples included.
        Several steps in one process (local jobs): the process peak if this step raised it, else the sampled peak."""
        ru = peak_rss_mb()
        sampled = max((m for _, m in self.samples), default=None)
        return ru if ru > self.peak0 + 1 or sampled is None else sampled

    def doc(self, status: str) -> dict:
        run_s = time.monotonic() - self.t0
        cpu = cpu_s() - self.c0
        peak = self.peak_mb()
        total = time.monotonic() - PROCESS_T0
        vcpu, mem = self.limits.get("vcpu"), self.limits.get("memory_mb")
        return {"run_id": self.run_id, "step": self.step, "attempt": self.attempt, "status": status,
                "updated_at": utc_now_iso(), "host": socket.gethostname(), "task": self.limits,
                "phases": {**self.phases, "run_s": round(run_s, 2), "total_s": round(total, 2)},
                "cpu_s": round(cpu, 2), "cpu_share": round(cpu / (run_s * (vcpu or 1)), 3) if run_s > 0 else None,
                "peak_rss_mb": round(peak, 1), "peak_share": round(peak / mem, 3) if mem else None,
                "cost_usd": cost_usd(total, self.limits), "rss_samples": self.samples}

    def finish(self, status: str) -> dict:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._sample()
        doc = self.doc(status)
        if self.write:
            self._emit(doc)
        return doc


def after_upload(doc: dict, upload_s: float) -> dict:
    """The task's last phase (database and record upload): total time and cost up to now."""
    total = time.monotonic() - PROCESS_T0
    doc = {**doc, "updated_at": utc_now_iso(),
           "phases": {**doc["phases"], "upload_s": round(upload_s, 2), "total_s": round(total, 2)}}
    doc["cost_usd"] = cost_usd(total, doc.get("task") or {})
    return doc


# ---- the report -------------------------------------------------------------------------------------------------
def _when(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def _dur(s: float | None) -> str:
    if s is None:
        return "–"
    return f"{s:.0f}s" if s < 90 else f"{int(s // 60)}m{int(s % 60):02d}s"


def load_docs(bucket=None, days: float = 2.0, root: Path | None = None) -> list[dict]:
    """Perf documents of the last `days`: from the state bucket (runs/*/perf/), or local records."""
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
    docs = []
    if bucket is not None:
        for key, modified in bucket.list("runs/"):
            if "/perf/" in key and modified >= cutoff:
                docs.append(json.loads(bucket.get_bytes(key)[0]))
    else:
        for p in (root or Path("logs/jobs")).glob("*/perf/*.json"):
            d = json.loads(p.read_text())
            if _when(d["updated_at"]) >= cutoff:
                docs.append(d)
    return sorted(docs, key=lambda d: d["attempt"])


def report(docs: list[dict], job: str | None = None, now: dt.datetime | None = None) -> str:
    from pipeline.job.steps import JOBS
    job_of = {s: j for j, steps in JOBS.items() for s in steps}
    now = now or dt.datetime.now(dt.UTC)
    docs = [d for d in docs if job is None or job_of.get(d["step"]) == job]
    if not docs:
        return "no performance documents in that period"
    lines = [f"{'job':<11} {'started (UTC)':<14} {'step':<10} {'status':<9} {'total':>7} {'run':>7} {'cpu':>5} "
             f"{'peak MB / limit':>19} {'db MB':>6} {'down/up s':>10} {'cost $':>9}"]
    for d in docs:
        status = d["status"]
        if status == "running" and (now - _when(d["updated_at"])).total_seconds() > 3 * WRITE_S:
            status = "killed?"                           # stopped writing without a final document
        ph, mem = d.get("phases", {}), (d.get("task") or {}).get("memory_mb")
        peak = f"{d['peak_rss_mb']:.0f}" + (f" / {mem:.0f} ({d['peak_share']:.0%})" if mem else "")
        updown = f"{ph.get('download_s', 0):.0f} / {ph.get('upload_s', 0):.0f}" if "download_s" in ph else "–"
        lines.append(f"{job_of.get(d['step'], '?'):<11} {d['attempt'][5:16].replace('T', ' '):<14} {d['step']:<10} "
                     f"{status:<9} {_dur(ph.get('total_s')):>7} {_dur(ph.get('run_s')):>7} "
                     f"{(d['cpu_share'] or 0):>5.0%} {peak:>19} {ph.get('db_mb', 0) or 0:>6.0f} {updown:>10} "
                     f"{d['cost_usd'] if d['cost_usd'] is not None else '–':>9}")
    by_step: dict[str, list[dict]] = {}
    for d in docs:
        by_step.setdefault(d["step"], []).append(d)
    lines += ["", f"{'per step':<11} {'runs':>5} {'median total':>13} {'max total':>10} {'max peak':>9} {'cost/run $':>11}"]
    for s, ds in by_step.items():
        totals = sorted(d["phases"].get("total_s") or 0 for d in ds)
        shares = [d["peak_share"] for d in ds if d.get("peak_share") is not None]
        costs = [d["cost_usd"] for d in ds if d.get("cost_usd") is not None]
        lines.append(f"{s:<11} {len(ds):>5} {_dur(totals[len(totals) // 2]):>13} {_dur(totals[-1]):>10} "
                     f"{(f'{max(shares):.0%}' if shares else '–'):>9} "
                     f"{(f'{sum(costs) / len(costs):.4f}' if costs else '–'):>11}")
    costs = [d["cost_usd"] for d in docs if d.get("cost_usd") is not None]
    if costs:
        span_d = max((now - _when(docs[0]["attempt"])).total_seconds() / 86400, 1 / 24)
        lines += ["", f"compute ${sum(costs):.3f} over {span_d:.1f} days → ≈ ${sum(costs) / span_d * 30.4:.2f} a month "
                      f"at this rate (excludes container starts)"]
    return "\n".join(lines)
