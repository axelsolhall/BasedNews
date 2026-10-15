"""H3 prefetch: fetch the history pages for every day ahead of the replay, a month at a time (A61).

    BASEDNEWS_IPV4=1 PYTHONPATH=. uv run python -m pipeline.history.prefetch [--start 2015-11-01] [--end YYYY-MM-DD]
    → articles in data/history/history.sqlite (headline, description, publish time); fetch_progress per month (resumable)

Needs no GPU, so it runs anywhere (the maintainer's Mac) while the PC is set up; the replay then runs with --prefetched.
A month at a time keeps all ~200 sites busy at once instead of every day waiting for its slowest site.
"""
from __future__ import annotations

import argparse
import datetime as dt
import time

from pipeline import store
from pipeline.history import stories as H2
from pipeline.history.fetch import fetch_many
from pipeline.history.replay import DB, prefilter, store_pages
from pipeline.ingest.feeds import utc_now_iso

PROGRESS = """CREATE TABLE IF NOT EXISTS fetch_progress (
    month TEXT PRIMARY KEY, gdelt INTEGER, candidates INTEGER, ok INTEGER, stored INTEGER, seconds REAL, done_at TEXT)"""


def months(start: dt.date, end: dt.date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + (m == 12), m % 12 + 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2015-11-01")
    ap.add_argument("--end", default=dt.date.today().isoformat())
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--workers", type=int, default=128)
    args = ap.parse_args()
    conn = store.connect(args.db)
    conn.execute(PROGRESS)
    done = {r["month"] for r in conn.execute("SELECT month FROM fetch_progress")}
    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    cache = {}
    for y, m in months(start, end):
        key = f"{y}-{m:02d}"
        if key in done:
            continue
        if y not in cache:
            cache.clear()
            cache[y] = H2.load_year(y)
        days = [d for d in sorted(cache[y]) if d.month == m and start <= d <= end]
        gdelt = sum(len(cache[y][d]) for d in days)
        rows = [r for d in days for r in prefilter(cache[y][d])]
        t0 = time.monotonic()
        pages = fetch_many([r["url"] for r in rows], workers=args.workers)
        stored = len(store_pages(conn, rows, pages))
        secs = time.monotonic() - t0
        ok = sum(p is not None for p in pages.values())
        conn.execute("INSERT OR REPLACE INTO fetch_progress VALUES (?,?,?,?,?,?,?)",
                     (key, gdelt, len(rows), ok, stored, secs, utc_now_iso()))
        conn.commit()
        print(f"{key}: GDELT {gdelt:,} → candidates {len(rows):,} → pages {ok:,} → stored {stored:,} in {secs / 60:.0f} min",
              flush=True)


if __name__ == "__main__":
    main()
