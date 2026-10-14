"""History coverage: on how many days per year does the Internet Archive hold a front page of each history outlet?

    BASEDNEWS_IPV4=1 PYTHONPATH=. uv run python -m pipeline.backfill.coverage [--from 2010] [--only ID ...]
    → data/history_coverage.json (resumable) and one line per outlet: days captured in each year

One CDX query per address and year (short and predictable; a 17-year query can take minutes), collapsed to one capture
per day, status 200 only. The other spelling of the host (www / bare) is tried only when the first is thin. Four
outlets at a time. The result says from when each outlet can have a daily history; an outlet that moved address
(a new domain) shows a gap until the old address is added.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

from pipeline.net import fetch

CDX = ("https://web.archive.org/cdx/search/cdx?url={url}&from={y}0101&to={y}1231"
       "&filter=statuscode:200&collapse=timestamp:8&fl=timestamp")
OUT = Path("data/history_coverage.json")
LOCK = threading.Lock()


def days(url: str, year: int) -> int | None:
    for attempt in range(3):
        try:
            return len(fetch(CDX.format(url=url, y=year), timeout=45)[0].split())
        except Exception:
            time.sleep(5 * (attempt + 1))
    return None                                              # unknown, not zero


def variants(home: str) -> list[str]:
    p = urlsplit(home)
    other = p.netloc[4:] if p.netloc.startswith("www.") else "www." + p.netloc
    path = p.path.rstrip("/")
    return [p.netloc + path + "/", other + path + "/"]


def survey(o: dict, years: range) -> dict:
    best = None
    for v in variants(o["homepage"]):
        got = {str(y): days(v, y) for y in years}
        total = sum(n or 0 for n in got.values())
        if best is None or total > best[1]:
            best = (v, total, got)
        if total >= 0.5 * 365 * len(years):                  # good enough: skip the other spelling
            break
    return {"country": o["country"], "name": o["name"], "address": best[0], "days": best[2]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", type=int, default=2010)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    years = range(args.start, dt.date.today().year + 1)
    outlets = json.loads(Path("data/history_outlets.json").read_text())["outlets"]
    done = json.loads(OUT.read_text()) if OUT.exists() else {}
    todo = [o for o in outlets if (o["id"] in args.only if args.only else o["id"] not in done)]

    def run(o):
        r = survey(o, years)
        with LOCK:
            done[o["id"]] = r
            OUT.write_text(json.dumps(done, indent=1, ensure_ascii=False))
            print(f"{o['id']:<28} {r['address']:<36} " + " ".join(f"{'?' if n is None else n:>3}" for n in r["days"].values()),
                  flush=True)

    with ThreadPoolExecutor(args.workers) as ex:
        list(ex.map(run, todo))


if __name__ == "__main__":
    main()
