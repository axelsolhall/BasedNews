#!/usr/bin/env python3
import datetime as dt
import subprocess
import sys
import time


TASKS = [
    {
        "name": "ingest_feeds",
        "cmd": "python scripts/ingest_feeds.py",
        "interval_minutes": 720,
        "run_on_start": True,
    }
]
# TASKS = [
#     {
#         "name": "test 1",
#         "cmd": "echo 'Hello from test 1'",
#         "interval_minutes": 1,
#         "run_on_start": True,
#     },
#     {
#         "name": "test 2",
#         "cmd": "echo 'Hello from test 2'",
#         "interval_minutes": 2,
#         "run_on_start": False,
#     },
#     {
#         "name": "test 3",
#         "cmd": "echo 'Hello from test 3'",
#         "interval_minutes": 3,
#         "run_on_start": True,
#     },
# ]


class Task:
    def __init__(self, name, cmd, interval_minutes, run_on_start):
        if interval_minutes <= 0:
            raise ValueError(f"interval_minutes must be > 0 for {name}")
        self.name = name
        self.cmd = cmd
        self.interval = dt.timedelta(minutes=interval_minutes)
        now = dt.datetime.now()
        self.next_run = now if run_on_start else now + self.interval
        self.process = None
        self.last_started = None


def build_tasks():
    tasks = []
    for entry in TASKS:
        tasks.append(
            Task(
                name=entry["name"],
                cmd=entry["cmd"],
                interval_minutes=entry["interval_minutes"],
                run_on_start=entry.get("run_on_start", True),
            )
        )
    if not tasks:
        raise ValueError("TASKS is empty")
    return tasks


def start_task(task):
    start = dt.datetime.now()
    cmd = task.cmd
    cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)
    print(f"[{start.isoformat()}] START {task.name} -> {cmd_str}")
    try:
        if isinstance(cmd, str):
            process = subprocess.Popen(cmd, shell=True)
        else:
            process = subprocess.Popen(cmd)
    except Exception as exc:
        end = dt.datetime.now()
        print(f"[{end.isoformat()}] ERROR {task.name} -> {exc}")
        return None
    task.process = process
    task.last_started = start
    return process


def poll_task(task):
    if task.process is None:
        return
    rc = task.process.poll()
    if rc is None:
        return
    end = dt.datetime.now()
    elapsed = (end - task.last_started).total_seconds()
    print(f"[{end.isoformat()}] END {task.name} rc={rc} elapsed={elapsed:.1f}s")
    task.process = None
    task.last_started = None


def next_wake(tasks, now):
    return min(task.next_run for task in tasks)


def scheduler_loop(tasks):
    while True:
        now = dt.datetime.now()
        for task in tasks:
            poll_task(task)
        due = [task for task in tasks if task.next_run <= now]
        if not due:
            wake_at = next_wake(tasks, now)
            sleep_seconds = max(0.0, (wake_at - now).total_seconds())
            time.sleep(sleep_seconds)
            continue
        for task in due:
            if task.process is None:
                task.next_run = dt.datetime.now() + task.interval
                start_task(task)


def main():
    tasks = build_tasks()
    try:
        scheduler_loop(tasks)
    except KeyboardInterrupt:
        print("Scheduler stopped")
        sys.exit(0)


if __name__ == "__main__":
    main()
