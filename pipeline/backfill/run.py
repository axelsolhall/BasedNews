"""M2b run: sparse 10-year historical sample per outlet from the Wayback Machine. Resumable.

    uv run python -m pipeline.backfill.run [--years 10] [--per-snapshot 6] [--level yearly|quarterly|monthly|all] [--only ID ...]

Design (A23):
- Coarse-to-fine grid: yearly → quarterly → monthly sample dates (the 15th), so a partial run is still an even grid.
- Synchronised: every outlet in a country is sampled on the SAME date, so front pages share events → same-event pairs (A02)
  exist historically too.
- Per (outlet, date): archived RSS feed if captured, else archived homepage → candidate article URLs → sample k → fetch the
  archived article → extract text (only if the publisher allows TDM today, A10) → store with published ≈ snapshot date.
- Progress is stored in table `backfill_jobs`; re-running skips finished (outlet, date) jobs.
"""
from __future__ import annotations

import argparse
import datetime as dt
import random
import re
import time
from urllib.parse import urljoin, urlparse

from pipeline import store
from pipeline.backfill import wayback
from pipeline.contracts import Article
from pipeline.ingest.extract import clean_text, text_from_html
from pipeline.ingest.feeds import parse_feed, utc_now_iso
from pipeline.ingest.run import MIN_TEXT_CHARS, article_id
from pipeline.sources.registry import load_outlets

JOBS_SCHEMA = """CREATE TABLE IF NOT EXISTS backfill_jobs (
    outlet_id TEXT, date TEXT, status TEXT, n_articles INTEGER, note TEXT, done_at TEXT,
    PRIMARY KEY (outlet_id, date))"""


def grid(years: int, level: str) -> list[str]:
    today = dt.date.today()
    dates = []
    for y in range(today.year - years, today.year + 1):
        months = {"yearly": [6], "quarterly": [2, 5, 8, 11], "monthly": list(range(1, 13))}[level]
        for m in months:
            d = dt.date(y, m, 15)
            if d < today - dt.timedelta(days=30):
                dates.append(d.strftime("%Y%m%d"))
    return dates


def article_links(html: str, base: str) -> list[str]:
    """Heuristic for archived homepages: same-site links that look like articles (long path, digits or long slug)."""
    host = urlparse(base).netloc.replace("www.", "")
    out = []
    for href in re.findall(r'href=["\']([^"\'#]+)["\']', html, flags=re.I):
        u = urljoin(base, href)
        p = urlparse(u)
        if host not in p.netloc or len(p.path) < 25:
            continue
        if re.search(r"\d{3,}|[a-z]+-[a-z]+-[a-z]+-[a-z]+", p.path):
            out.append(f"{p.scheme}://{p.netloc}{p.path}")
    return list(dict.fromkeys(out))


def backfill_one(o, date: str, k: int, allow_text: bool, conn) -> tuple[int, str]:
    # 1) archived feed first, then archived homepage
    items, note = [], ""
    for feed in o.feeds:
        snap = wayback.near(feed, date)
        if snap:
            try:
                items = [{"url": i["url"], "title": i.get("title"), "summary": i.get("summary")}
                         for i in parse_feed(snap[1]) if i.get("url")]
                note = f"feed@{snap[0]}"
            except Exception:
                items = []
        if items:
            break
    if not items and o.homepage:
        snap = wayback.near(o.homepage, date)
        if snap:
            items = [{"url": u, "title": None, "summary": None}
                     for u in article_links(snap[1].decode("utf-8", errors="ignore"), o.homepage)]
            note = f"home@{snap[0]}"
    if not items:
        return 0, "no snapshot within window"
    random.Random(f"{o.id}{date}").shuffle(items)
    added, tries = 0, 0
    for it in items:
        if added >= k or tries >= 3 * k:
            break
        if store.article_exists(conn, it["url"]):
            continue
        tries += 1
        try:
            snap = wayback.near(it["url"], date, window_days=60)
        except wayback.ArchiveUnavailable:
            continue        # one stubborn ARTICLE must not look like a global outage (bug seen 2026-10-11 03:00)
        if not snap:
            continue
        ts, raw = snap
        html = raw.decode("utf-8", errors="ignore")
        title = it["title"] or (re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S) or [None, ""])[1].strip()
        if not title:
            continue
        text, source = text_from_html(html) if allow_text else (None, "none")
        if not text or len(text) < MIN_TEXT_CHARS:
            text, source = None, "summary" if it.get("summary") else "none"
        published = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}T{ts[8:10]}:{ts[10:12]}:{ts[12:14]}Z"   # A23: capture time ≈ publish time
        if store.insert_article(conn, Article(
            id=article_id(it["url"], title), outlet_id=o.id, country=o.country, url=it["url"], title=clean_text(title)[:500],
            published=published, summary=clean_text(it.get("summary")), full_text=text,
            feed_url=f"wayback:{note}", ingested_at=utc_now_iso(), text_source=f"wayback-{source}",
        )):
            added += 1
        conn.commit()   # never hold the write lock across slow archive.org downloads (bug 2026-10-11: blocked live ingest)
    return added, note


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--per-snapshot", type=int, default=6)
    ap.add_argument("--level", default="all", choices=["yearly", "quarterly", "monthly", "all"])
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()
    conn = store.connect()
    conn.execute(JOBS_SCHEMA)
    health = store.latest_health(conn)
    outlets = [o for o in load_outlets() if not args.only or o.id in args.only]
    # finished = ok/empty; "error" jobs are retried on every pass
    done = {(r[0], r[1]) for r in conn.execute("SELECT outlet_id, date FROM backfill_jobs WHERE status IN ('ok','empty')")}
    levels = ["yearly", "quarterly", "monthly"] if args.level == "all" else [args.level]
    for level in levels:
        for date in grid(args.years, level):                  # same date across all outlets (A23)
            for o in outlets:
                if (o.id, date) in done:
                    continue
                allow = health.get(o.id).full_text_allowed if o.id in health else False
                waits = 0
                while True:
                    try:
                        n, note = backfill_one(o, date, args.per_snapshot, allow, conn)
                        status = "ok" if n else "empty"
                        break
                    except wayback.ArchiveUnavailable as e:
                        waits += 1
                        if waits > 3:     # one stubborn feed/homepage must not block the whole run
                            n, note, status = 0, f"gave up after 3 outage waits: {e}"[:200], "error"
                            break
                        print(f"archive.org unavailable ({e}); sleeping 10 min [{waits}/3]", flush=True)
                        time.sleep(600)
                        continue
                    except Exception as e:
                        n, note, status = 0, f"{type(e).__name__}: {e}"[:200], "error"
                        break
                conn.execute("INSERT OR REPLACE INTO backfill_jobs VALUES (?,?,?,?,?,?)",
                             (o.id, date, status, n, note, utc_now_iso()))
                conn.commit()
                done.add((o.id, date))
                print(f"{level:<9} {date} {o.id:<16} {status:<6} +{n} {note}", flush=True)


if __name__ == "__main__":
    main()
