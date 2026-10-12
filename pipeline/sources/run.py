"""M1 run: check every outlet's feeds and text-processing policy; store FeedHealth.

    uv run python -m pipeline.sources.run [--only OUTLET_ID ...]
"""
from __future__ import annotations

import argparse
import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from pipeline import net, store
from pipeline.contracts import FeedHealth, Outlet
from pipeline.ingest.feeds import parse_feed, to_utc_iso, utc_now_iso
from pipeline.sources.registry import load_outlets


def check_outlet(o: Outlet) -> FeedHealth:
    errors, ok, items, newest, sample_url = [], 0, 0, None, None
    for feed in o.feeds:
        try:
            body, _ = net.fetch(feed)
            parsed = parse_feed(body)
            if not parsed:
                errors.append(f"no items: {feed}")
                continue
            ok += 1
            items += len(parsed)
            for it in parsed:
                sample_url = sample_url or it.get("url")
                ts = to_utc_iso(it.get("published"))
                if ts and (newest is None or ts > newest):
                    newest = ts
        except Exception as e:
            errors.append(f"{type(e).__name__}: {feed}")

    robots, tdm = None, None
    if sample_url:
        robots = net.robots_allows(sample_url)
        u = urlparse(sample_url)
        tdm = net.tdm_reserved_for_origin(f"{u.scheme}://{u.netloc}")
        try:
            _, headers = net.fetch(sample_url, timeout=15)
            header_tdm = net.tdm_reserved_from_headers(headers)
            tdm = header_tdm if header_tdm is not None else tdm
        except Exception as e:
            errors.append(f"sample article {type(e).__name__}")

    age_h = None
    if newest:
        age_h = (dt.datetime.now(dt.UTC) - dt.datetime.fromisoformat(newest.replace("Z", "+00:00"))).total_seconds() / 3600
    # A10 policy: process full text only if robots.txt does not forbid it and no TDM reservation is declared.
    allowed = robots is not False and tdm is not True
    return FeedHealth(o.id, utc_now_iso(), ok, len(o.feeds), items, age_h, robots, tdm, allowed, tuple(errors))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()
    outlets = [o for o in load_outlets() if not args.only or o.id in args.only]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(check_outlet, outlets))
    conn = store.connect()
    for h in results:
        store.save_health(conn, h)
    conn.commit()
    print(f"checked {len(results)} outlets -> data/basednews.sqlite (feed_health). Run `python -m pipeline.sources.eval`.")


if __name__ == "__main__":
    main()
