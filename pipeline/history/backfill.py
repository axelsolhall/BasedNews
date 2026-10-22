"""H3b backfill: more history articles where the first prefetch got few (A75) — the same candidates, tried harder.

    BASEDNEWS_IPV4=1 PYTHONPATH=. uv run python -m pipeline.history.backfill live --start 2019-01 --end 2019-12
    BASEDNEWS_IPV4=1 PYTHONPATH=. uv run python -m pipeline.history.backfill wayback --start 2019-01 --end 2019-12
    PYTHONPATH=. uv run python -m pipeline.history.backfill report [--year 2019]     (eval: per method and outlet)

The candidates are the prefetch's own (replay.prefilter over GDELT's day: ≤ CAP per outlet per day, articles that
share people, places or words with another outlet's that day), cached per month in data/history/candidates/. A
candidate already stored is never fetched again; one a method has tried is never tried again by it
(backfill_tries). Pages are stored exactly as the prefetch stores them (headline, description, publish time; a page
dated more than MAX_GAP_DAYS from GDELT's sighting is dropped), with feed_url saying where they came from.

  live     the publisher again, patiently: a site is not skipped after 3 failures (the prefetch's rule, which lost
           whole months of sites with a few slow pages), and a longer timeout.
  sitemap  where we have fewer than SITEMAP_BELOW of an outlet's articles in a month (none in some years, none
           ever for the outlets it was not asked about), the outlet's own sitemaps (pipeline.history.sitemaps) give
           the month's articles; the prefilter picks among them with GDELT's other outlets that day, and the pages
           come from the publisher. A page without its own publish date in the month is dropped (a sitemap's date
           may be its last edit).
  wayback  the Internet Archive's copy closest to GDELT's sighting (web.archive.org/web/<time>id_/<url>: the page as
           archived, without the archive's frame) — for dead links (404) and sites that refuse us (403). A copy that
           redirected elsewhere (a homepage) is not the article and is dropped.
  ccrawl   Common Crawl's copy from the crawls after GDELT's sighting (pipeline.history.ccrawl): the same rules as
           wayback (after live, sites that neither refuse nor disallow us, a 200 capture of this address), but a
           host's index is read once per crawl and a page is one range request — eval 2019-10, 60 dead links of
           Adevărul, Cyprus Mail, Novini: 27 found, all the article, 22 s.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import unquote, urlsplit

from pipeline import net, store
from pipeline.history import ccrawl, quality
from pipeline.history.replay import prefilter as _prefilter
from pipeline.history import stories as H2
from pipeline.history.fetch import SKIPPED as SKIPPED_SITE, _Polite, fetch_iter, parse, read_head
from pipeline.history.replay import DB, store_pages
from pipeline.ingest.feeds import utc_now_iso

CANDIDATES = Path("data/history/candidates")
TRIES = """CREATE TABLE IF NOT EXISTS backfill_tries (
    url TEXT NOT NULL, method TEXT NOT NULL, ok INTEGER NOT NULL, at TEXT NOT NULL, PRIMARY KEY (url, method))"""
PROGRESS = """CREATE TABLE IF NOT EXISTS backfill_progress (
    month TEXT NOT NULL, method TEXT NOT NULL, missing INTEGER, tried INTEGER, ok INTEGER, stored INTEGER,
    seconds REAL, done_at TEXT, PRIMARY KEY (month, method))"""
SITE_START, SITE_MOST = 2, 8                # requests in flight per site: from 2, adaptive up to 8, halved when the
                                           # site says slow down (fetch._Polite; the maintainer: "2-8x, dynamic")
LIVE_CHUNK = 4000                          # pages between commits (a month was one batch: a hang lost it all)
LIVE_GIVE_UP, LIVE_TIMEOUT = 25, 15        # consecutive failures before a site is skipped for the month; seconds
DEAD_AFTER = 25                            # a site that gave nothing in this many tries in a run is skipped for the
                                           # rest of it (Jan 2019: 201 of 332 sites gave nothing, 19,448 tries)
RETRY_BEFORE = "2026-10-20T00:35:00Z"      # --retry-hosts asks again only for failures recorded before this (the
                                           # reader and give-up fixes); a page that fails again is not re-asked
READER_SINCE = "2026-10-19T22:30:00Z"      # tries before this read only a page's first 256 kB (sites whose metadata
                                           # comes later failed): they do not make a site dead
WP_OUTLETS = {"newsbook-mal", "ziarul-de-garda-mol", "rtsh-alb", "news247-gre", "okdiario"}   # wpapi answers
WP_BELOW = 300                              # their posts are cheap (100 a request, no page fetch): up to this many
SITEMAP_MONTH_MAX = 20_000                  # an outlet's sitemap entries in one month above which their dates are
                                            # not its articles' (2019-10: 2.6 M entries from 32 outlets killed the pass)
SITEMAP_BELOW = 100                         # stored articles of an outlet in a month below which its sitemaps are
                                            # read (was: GDELT's listings, which missed outlets whose links are dead)
WAYBACK_WORKERS = 1                         # requests in flight to web.archive.org (≈ 2–4 s each)
WAYBACK_GAP = 6.0                           # seconds between requests: the archive refuses connections from a client
                                            # above ≈ 15 a minute (8 in flight, 1 per 1.5 s, 2 in flight at 1 per 4.5
                                            # s all got refused; 2026-10-20) — ≈ 500 an hour
WAYBACK_BELOW = 100                         # only outlets with fewer stored articles in the month (the archive is
                                            # slow: ≈ 500 an hour; a later pass can raise it)
CCRAWL_BELOW = 10 ** 9                      # Common Crawl is cheap: every outlet's dead links, however many we have
WAYBACK_CHUNK = 200                         # archive requests between commits


SKIP_HOSTS = Path("data/history/backfill_skip_hosts.txt")   # sites the shared live/sitemap passes leave to a run
                                            # of their own (--outlets): one slow site (mbl.is: ≈ 24 s a page, one at
                                            # a time) held every pass ≈ 1 h at each month's end. Read every month:
                                            # running passes pick up a change. Their pages stay untried, not failed.
                                            # A run of chosen --outlets that names a site in --retry-hosts asks it anyway (its own chain).

PREFILTER_LOCK = Path("data/history/.prefilter.lock")


def prefilter(rows: list[dict]) -> list[dict]:
    """replay.prefilter, one process at a time on this machine: one GDELT day's exact clustering peaks at ≈ 10 GB
    (similarity of ≈ 22k articles and its largest component), and the passes run side by side."""
    import fcntl
    with open(PREFILTER_LOCK, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            return _prefilter(rows)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def months(start: str, end: str):
    y, m = map(int, start.split("-"))
    while f"{y}-{m:02d}" <= end:
        yield y, m
        y, m = (y + (m == 12), m % 12 + 1)


def month_rows(y: int, m: int, cache: dict) -> dict:
    """GDELT's rows of the month by day, one month held at a time (a whole year took ≈ 5.5 GB a process)."""
    if (y, m) not in cache:
        cache.clear()
        cache[(y, m)] = H2.load_month(y, m)
    return cache[(y, m)]


def candidates(y: int, m: int, year_cache: dict) -> list[dict]:
    """The month's prefetch candidates (cached: the prefilter takes ≈ 3 min a month)."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    path = CANDIDATES / f"{y}-{m:02d}.parquet"
    more = H2.MORE / f"{y}-{m:02d}.parquet"                    # raw-file rows that came after the cache: redo it
    if path.exists() and not (more.exists() and more.stat().st_mtime > path.stat().st_mtime):
        return pq.read_table(path).to_pylist()
    days = month_rows(y, m, year_cache)
    rows = [r for d in sorted(days) if d.month == m for r in prefilter(days[d])]
    CANDIDATES.mkdir(parents=True, exist_ok=True)
    keep = [{k: r[k] for k in ("seen", "url", "outlet", "country")} for r in rows]
    tmp = path.with_suffix(f".{os.getpid()}.part")    # passes run side by side: never a half file
    pq.write_table(pa.Table.from_pylist(keep), tmp)
    tmp.rename(path)
    return keep


def sitemap_candidates(y: int, m: int, year_cache: dict, have: Counter,
                       only: set[str] | None = None) -> list[dict]:
    """The month's articles of the outlets we have few of (have: stored per outlet), from their sitemaps, as the
    prefilter picks them."""
    from pipeline.history import sitemaps as SM
    from pipeline.sources.registry import load_outlets
    days = {d: rows for d, rows in month_rows(y, m, year_cache).items() if d.month == m}
    outlets = [o for o in load_outlets() if (only is None or o.id in only) and have[o.id] < SITEMAP_BELOW]
    with ThreadPoolExecutor(16) as pool:                            # a site that refuses us: its sitemaps are no use
        gates = list(pool.map(lambda o: site_gate(o.homepage), outlets))
    outlets = [o for o, g in zip(outlets, gates) if g == "ok"]
    with ThreadPoolExecutor(16) as pool:
        found = list(pool.map(lambda o: SM.month_urls(o.id, o.homepage, y, m), outlets))
    extra = defaultdict(list)
    for o, urls in zip(outlets, found):
        if len(urls) > SITEMAP_MONTH_MAX:                    # a whole archive re-stamped with one date: not the month's
            print(f"  {o.id}: {len(urls):,} sitemap entries dated {y}-{m:02d} — dates not trusted, skipped", flush=True)
            continue
        for url, when in urls:
            extra[when.date()].append({"seen": when.astimezone(dt.timezone.utc).replace(tzinfo=None), "url": url,
                                       "outlet": o.id, "country": o.country, "persons": "", "orgs": "", "places": ""})
    print(f"  {y}-{m:02d} sitemaps: {sum(len(v) for v in extra.values()):,} articles from "
          f"{sum(bool(u) for u in found)} of {len(outlets)} outlets", flush=True)
    picked = []
    for d, ex in sorted(extra.items()):
        mine = {r["url"] for r in ex}
        picked += [r for r in prefilter(days.get(d, []) + ex) if r["url"] in mine]
    return picked


def wp_candidates(y: int, m: int, year_cache: dict, have: Counter,
                  only: set[str] | None = None) -> tuple[list[dict], dict]:
    """The month's posts of the WordPress outlets we have < WP_BELOW of (pipeline.history.wpapi: address, publish
    time, title, excerpt — no page fetch), as the prefilter picks them; and their pages."""
    from pipeline.history import wpapi
    from pipeline.sources.registry import load_outlets
    days = {d: rows for d, rows in month_rows(y, m, year_cache).items() if d.month == m}
    outlets = [o for o in load_outlets() if o.id in WP_OUTLETS and (only is None or o.id in only)
               and have[o.id] < WP_BELOW]
    with ThreadPoolExecutor(8) as pool:
        found = list(pool.map(lambda o: wpapi.month_pages(o.homepage, y, m), outlets))
    extra, pages = defaultdict(list), {}
    for o, got in zip(outlets, found):
        for url, page in got.items():
            when = dt.datetime.fromisoformat(page.published.replace("Z", "+00:00"))
            extra[when.date()].append({"seen": when, "url": url, "outlet": o.id, "country": o.country,
                                       "persons": "", "orgs": "", "places": ""})
            pages[url] = page
    print(f"  {y}-{m:02d} wordpress: {len(pages):,} posts from {sum(bool(g) for g in found)} of {len(outlets)} outlets",
          flush=True)
    picked = []
    for d, ex in sorted(extra.items()):
        mine = {r["url"] for r in ex}
        picked += [r for r in prefilter(days.get(d, []) + ex) if r["url"] in mine]
    return picked, pages


def in_month(page, y: int, m: int) -> bool:
    """The page's own publish date falls in the month (± MAX_GAP_DAYS)."""
    from pipeline.history.replay import MAX_GAP_DAYS
    if not page or not page.published:
        return False
    when = dt.datetime.fromisoformat(page.published.replace("Z", "+00:00"))
    lo = dt.datetime(y, m, 1, tzinfo=dt.timezone.utc) - dt.timedelta(days=MAX_GAP_DAYS)
    hi = dt.datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(days=MAX_GAP_DAYS)
    return lo <= when < hi


def missing(conn, rows: list[dict], method: str, retry_hosts: set[str] | None = None,
            only_retry: bool = False) -> list[dict]:
    """Candidates not stored and not yet tried by this method — or tried and failed before RETRY_BEFORE on one of
    retry_hosts (sites whose failures came from our reader or our give-up rule, since fixed). only_retry: just
    those."""
    urls = [r["url"] for r in rows]
    have, again = set(), set()
    for k in range(0, len(urls), 900):
        ch = urls[k:k + 900]
        q = ",".join("?" * len(ch))
        have |= {u for (u,) in conn.execute(f"SELECT url FROM articles WHERE url IN ({q})", ch)}
        for u, ok, at in conn.execute(f"SELECT url, ok, at FROM backfill_tries WHERE method = ? AND url IN ({q})",
                                      (method, *ch)):
            if not ok and retry_hosts and at < RETRY_BEFORE and urlsplit(u).netloc.lower() in retry_hosts:
                again.add(u)
            else:
                have.add(u)
    rows = [r for r in rows if r["url"] not in have]
    return [r for r in rows if r["url"] in again] if only_retry else rows


def skip_hosts() -> set[str]:
    try:
        return {ln.split("#")[0].strip().lower() for ln in SKIP_HOSTS.read_text().splitlines()} - {""}
    except FileNotFoundError:
        return set()


REFUSED = {401, 403, 406, 451}
SKIP = "skip"                              # not tried, the publisher declines us (A33): not recorded
RETRY = "retry"                            # not answered (the archive paused us): not recorded, asked again later


def site_gate(url: str) -> str:
    """'ok', 'robots' (the publisher's robots.txt disallows us: it also governs archived copies, A10/A28) or
    'refused' (one live request is refused, or answered by a bot wall: A33 — a block is the publisher declining
    automated access)."""
    if net.robots_allows(url) is False:
        return "robots"
    try:
        req = urllib.request.Request(net.iri_to_uri(url), headers={"User-Agent": net.USER_AGENT, "Accept": "text/html"})
        with urllib.request.urlopen(req, timeout=LIVE_TIMEOUT) as r:
            page = parse(read_head(r).decode("utf-8", errors="replace"))
        return "refused" if page and quality.is_wall(page.title) else "ok"     # a bot wall answers 200 (A76)
    except urllib.error.HTTPError as e:
        return "refused" if e.code in REFUSED else "ok"            # 404, 410, 5xx: the page is gone, not refused
    except Exception:                                               # noqa: BLE001 — dead host, timeout
        return "ok"


class Wayback:
    """web.archive.org, politely: one request at a time every WAYBACK_GAP seconds at most, and a long pause when the
    archive says it is busy (429/503) or stops answering (it refuses connections from a client it finds too fast).
    A request the archive did not answer is RETRY: not recorded, so a later run asks again. Only sites that neither
    refuse us nor disallow us in robots.txt (site_gate, once per site per run)."""

    def __init__(self, workers: int = WAYBACK_WORKERS, gap: float = WAYBACK_GAP):
        self.workers, self.gap = workers, gap
        self.next_at, self.pause_until, self.guard = 0.0, 0.0, threading.Lock()
        self.gates: dict[str, str] = {}
        self.gate_lock = threading.Lock()

    def allowed(self, url: str) -> bool:
        host = urlsplit(url).netloc.lower()
        with self.gate_lock:
            if host not in self.gates:
                self.gates[host] = site_gate(url)
                if self.gates[host] != "ok":
                    print(f"  {host}: {self.gates[host]} — no archive copies", flush=True)
            return self.gates[host] == "ok"

    def _turn(self):
        """Wait for this request's slot: after any pause, and WAYBACK_GAP after the previous request."""
        with self.guard:
            at = max(self.next_at, self.pause_until, time.monotonic())
            self.next_at = at + self.gap
        time.sleep(max(0.0, at - time.monotonic()))

    def _pause(self, seconds: float, why: str):
        with self.guard:
            if self.pause_until < time.monotonic() + seconds:
                self.pause_until = time.monotonic() + seconds
                print(f"  archive {why}: pausing {seconds / 60:.0f} min", flush=True)

    def get(self, row: dict):
        url, seen = row["url"], row["seen"]
        if not self.allowed(url):
            return SKIP
        stamp = seen.strftime("%Y%m%d%H%M%S")
        wb = f"https://web.archive.org/web/{stamp}id_/{url}"
        for attempt in range(6):
            self._turn()
            try:
                req = urllib.request.Request(net.iri_to_uri(wb), headers={"User-Agent": net.USER_AGENT})
                with urllib.request.urlopen(req, timeout=40) as r:
                    final, body = r.geturl(), read_head(r).decode("utf-8", errors="replace")
                # the archived page must be this article, not where it redirected (a homepage, a consent wall)
                path = unquote(urlsplit(url).path).rstrip("/")
                if path and path not in unquote(urlsplit(final.split("id_/", 1)[-1]).path):
                    return None
                return parse(body)
            except urllib.error.HTTPError as e:
                if e.code in (429, 503, 504):
                    self._pause(60 * (attempt + 1), f"busy ({e.code})")
                    continue
                return None                                             # 404: the archive has no copy
            except urllib.error.URLError as e:
                if isinstance(e.reason, ConnectionRefusedError) or "refused" in str(e.reason).lower():
                    self._pause(1800, "refuses connections")       # a block lasts; asking again extends it
                else:
                    self._pause(30 * (attempt + 1), "timed out")
            except Exception:                                           # noqa: BLE001 — resets mid-read
                self._pause(30 * (attempt + 1), "reset")
        return RETRY

    def many(self, rows: list[dict]) -> dict:
        with ThreadPoolExecutor(self.workers) as pool:
            return dict(zip([r["url"] for r in rows], pool.map(self.get, rows)))


class CommonCrawl(Wayback):
    """data.commoncrawl.org through pipeline.history.ccrawl (its own pacing: IN_FLIGHT requests, pauses on 503),
    behind the same site gate. A request it did not answer is RETRY."""

    def __init__(self):
        super().__init__(workers=ccrawl.IN_FLIGHT, gap=0.0)

    def get(self, row: dict):
        if not self.allowed(row["url"]):
            return SKIP
        try:
            got = ccrawl.find(row["url"], row["seen"])
        except ccrawl.Busy:
            return RETRY
        except urllib.error.HTTPError:                                  # an index or WARC file it does not have
            return None
        return got[1] if got else None


def locked_retry(conn, fn, tries: int = 6):
    """fn() again after a rollback when SQLite says "database is locked": with eight passes writing side by side a
    write can lose the race even past busy_timeout (WAL: a stale read snapshot fails at once). Insert is idempotent."""
    for k in range(tries):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            if "locked" not in str(e) or k == tries - 1:
                raise
            conn.rollback()
            print(f"  database locked; again in {30 * (k + 1)} s", flush=True)
            time.sleep(30 * (k + 1))


def neediest_first(rows: list[dict], have: Counter) -> list[dict]:
    """Round-robin over outlets, the outlet with the fewest stored articles first: a slow source reaches every outlet
    before it deepens any."""
    by = defaultdict(list)
    for r in rows:
        by[r["outlet"]].append(r)
    order = sorted(by, key=lambda o: (have[o], o))
    out, k = [], 0
    while len(out) < len(rows):
        out += [by[o][k] for o in order if k < len(by[o])]
        k += 1
    return out


def run(method: str, start: str, end: str, db: str = str(DB), outlets: set[str] | None = None,
        retry_hosts: set[str] | None = None):
    conn = store.connect(db)
    conn.execute("PRAGMA busy_timeout = 300000")                # several passes write side by side: wait, don't fail
    conn.execute(TRIES)
    conn.execute(PROGRESS)
    done = {r[0] for r in conn.execute("SELECT month FROM backfill_progress WHERE method = ?", (method,))}
    year_cache: dict = {}
    junk = quality.junk_titles(conn)                            # A76: titles that are not headlines
    archive = {"wayback": Wayback, "ccrawl": CommonCrawl}.get(method, lambda: None)()
    live_done = {r[0] for r in conn.execute("SELECT month FROM backfill_progress WHERE method = 'live'")}
    gates: dict[str, str] = {}                                  # site_gate per site, once per run
    host_tries, host_ok = Counter(), Counter()                  # this run's record per site (live, sitemap)
    if method == "live":                                        # ... starting from the run's months already done
        for y, m in months(start, end):
            if f"{y}-{m:02d}" not in done or not (CANDIDATES / f"{y}-{m:02d}.parquet").exists():
                continue
            urls = [r["url"] for r in candidates(y, m, year_cache)]
            for k in range(0, len(urls), 900):
                ch = urls[k:k + 900]
                for u, ok in conn.execute(f"SELECT url, ok FROM backfill_tries WHERE method = 'live' AND at >= ? "
                                          f"AND url IN ({','.join('?' * len(ch))})", (READER_SINCE, *ch)):
                    host_tries[urlsplit(u).netloc.lower()] += 1
                    host_ok[urlsplit(u).netloc.lower()] += ok
    for y, m in months(start, end):
        key = f"{y}-{m:02d}"
        if method == "ccrawl" and ccrawl._blocked.is_set():
            break
        if key in done and outlets is None and not retry_hosts:
            continue
        only_retry = key in done and outlets is None
        if method in ("wayback", "ccrawl") and key not in live_done and outlets is None:
            print(f"{key} {method}: waits for live (the archive is the slow source: only what the publisher refused)")
            continue
        t0 = time.monotonic()
        wp_pages: dict = {}
        if method == "sitemap":
            rows = sitemap_candidates(y, m, year_cache, stored_by_outlet(conn, y, m, junk), outlets)
        elif method == "wpapi":
            rows, wp_pages = wp_candidates(y, m, year_cache, stored_by_outlet(conn, y, m, junk), outlets)
        else:
            rows = [r for r in candidates(y, m, year_cache) if outlets is None or r["outlet"] in outlets]
        if method in ("live", "sitemap"):                     # a chain of its own names its sites in retry_hosts
            skip = skip_hosts() - ((retry_hosts or set()) if outlets else set())
            rows = [r for r in rows if urlsplit(r["url"]).netloc.lower() not in skip]
        todo = missing(conn, rows, method, retry_hosts, only_retry)
        if method == "wpapi":
            batches = [todo]
        elif method in ("live", "sitemap"):
            dead = {h for h, n in host_tries.items() if n >= DEAD_AFTER and not host_ok[h]} - (retry_hosts or set())
            todo = [r for r in todo if urlsplit(r["url"]).netloc.lower() not in dead]
            first = {}                                          # A33: a site that refuses us is not asked page by page
            for r in todo:
                first.setdefault(urlsplit(r["url"]).netloc.lower(), r["url"])
            new = [h for h in first if h not in gates]
            with ThreadPoolExecutor(32) as pool:
                gates.update(zip(new, pool.map(lambda h: site_gate(first[h]), new)))
            shut = {h for h in first if gates[h] != "ok"}
            if shut:
                print(f"  {key} {method}: {len(shut)} sites refuse us or disallow us, not asked: "
                      f"{', '.join(sorted(shut)[:12])}{' …' if len(shut) > 12 else ''}", flush=True)
            todo = [r for r in todo if urlsplit(r["url"]).netloc.lower() not in shut]
            by_host = defaultdict(list)                         # every chunk spans all sites, so all run in parallel
            for r in todo:
                by_host[urlsplit(r["url"]).netloc.lower()].append(r)
            todo = [r for k in range(max(map(len, by_host.values()), default=0)) for v in by_host.values() if k < len(v)
                    for r in [v[k]]]
            polite = _Polite(1.0, LIVE_GIVE_UP, LIVE_TIMEOUT, SITE_START, SITE_MOST)
            polite.skipped = SKIPPED_SITE
            batches = streamed(fetch_iter([r["url"] for r in todo], 512, polite), todo)
        else:
            have = stored_by_outlet(conn, y, m, junk)
            below = WAYBACK_BELOW if method == "wayback" else CCRAWL_BELOW
            todo = neediest_first([r for r in todo if have[r["outlet"]] < below], have)
            batches = [todo[k:k + WAYBACK_CHUNK] for k in range(0, len(todo), WAYBACK_CHUNK)]
        tried = ok = n_stored = retry = 0
        by = Counter()
        for batch in batches:
            if method == "wpapi":
                pages = {r["url"]: wp_pages.get(r["url"]) for r in batch}
            elif method in ("live", "sitemap"):
                batch, pages = batch                    # (rows, pages) as they came back
            else:
                pages = archive.many(batch)
            retry += sum(p is RETRY for p in pages.values())
            if method == "ccrawl" and ccrawl._blocked.is_set():
                break                                   # blocked: nothing more is asked this run
            pages = {u: p for u, p in pages.items() if p is not SKIP and p is not RETRY and p is not SKIPPED_SITE}
            rows_by_url = {r["url"]: dict(r, persons=None, orgs=None, places=None) for r in batch}
            if method == "sitemap":                     # dated by the page itself, not by the sitemap
                pages = {u: (p if in_month(p, y, m) else None) for u, p in pages.items()}
                for u, p in pages.items():
                    if p:
                        rows_by_url[u]["seen"] = dt.datetime.fromisoformat(p.published.replace("Z", "+00:00"))
            def save():
                got = store_pages(conn, list(rows_by_url.values()), pages, junk=junk,
                                  source={"live": "gdelt", "wayback": "gdelt+wayback", "ccrawl": "gdelt+ccrawl",
                                          "sitemap": "sitemap",
                                          "wpapi": "wpapi"}[method])
                now = utc_now_iso()
                conn.executemany("INSERT OR REPLACE INTO backfill_tries VALUES (?,?,?,?)",
                                 [(u, method, int(p is not None), now) for u, p in pages.items()])
                conn.commit()
                return got
            stored = locked_retry(conn, save)
            for u, p in pages.items():
                h = urlsplit(u).netloc.lower()
                host_tries[h] += 1
                host_ok[h] += p is not None
            tried, n_stored = tried + len(pages), n_stored + len(stored)
            ok += sum(p is not None for p in pages.values())
            by += Counter(rows_by_url[u]["outlet"] for u, p in pages.items() if p)
            if method != "wpapi" and len(todo) > LIVE_CHUNK:
                print(f"  {key} {method}: {tried:,} of {len(todo):,} asked · pages {ok:,} · stored {n_stored:,}"
                      f"{f' · {retry:,} unanswered' if retry else ''}", flush=True)
        secs = time.monotonic() - t0
        if outlets is None and not only_retry and not retry:    # a month with unanswered requests is not done
            conn.execute("INSERT OR REPLACE INTO backfill_progress VALUES (?,?,?,?,?,?,?,?)",
                         (key, method, len(todo), tried, ok, n_stored, secs, utc_now_iso()))
            conn.commit()
        print(f"{key} {method}: missing {len(todo):,} → pages {ok:,} → stored {n_stored:,} in {secs / 60:.0f} min"
              f"{f' ({retry:,} unanswered: not done)' if retry else ''} · "
              f"most: {', '.join(f'{o} {n}' for o, n in by.most_common(5))}", flush=True)


def streamed(results, todo: list[dict]):
    """(rows, pages) every LIVE_CHUNK pages as they come back, so a slow site never holds up a commit."""
    rows = {r["url"]: r for r in todo}
    buf: dict = {}
    for u, p in results:
        buf[u] = p
        if len(buf) >= LIVE_CHUNK:
            yield [rows[k] for k in buf], buf
            buf = {}
    yield [rows[k] for k in buf], buf


def stored_by_outlet(conn, y: int, m: int, junk: set | None = None) -> Counter:
    """Articles per outlet in the month, not counting rows whose title is not a headline (A76)."""
    lo, hi = f"{y}-{m:02d}", f"{y + (m == 12)}-{m % 12 + 1:02d}"
    out = Counter()
    for o, t, n in conn.execute("SELECT outlet_id, title, count(*) FROM articles "
                                "WHERE published >= ? AND published < ? GROUP BY 1, 2", (lo, hi)):
        if not (junk and (o, t) in junk) and not quality.is_wall(t):
            out[o] += n
    return out


def report(db: str, year: str | None):
    conn = store.connect(db)
    q = "" if year is None else f" AND substr(published, 1, 4) = '{int(year)}'"
    rows = conn.execute(f"SELECT feed_url, count(*) FROM articles WHERE 1 {q} GROUP BY 1").fetchall()
    print("articles by source:", {r[0]: r[1] for r in rows})
    for r in conn.execute("SELECT method, count(*), sum(missing), sum(ok), sum(stored), sum(seconds) / 3600 "
                          "FROM backfill_progress GROUP BY 1"):
        print(f"{r[0]}: {r[1]} months · missing {r[2]:,} · pages {r[3]:,} · stored {r[4]:,} · {r[5]:.1f} h")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["live", "wayback", "ccrawl", "sitemap", "wpapi", "report"])
    ap.add_argument("--start", default="2015-11")
    ap.add_argument("--end", default=dt.date.today().strftime("%Y-%m"))
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--outlets", nargs="*", help="only these outlets (a month is then not marked done)")
    ap.add_argument("--year")
    ap.add_argument("--retry-hosts", nargs="*", help="also ask again for these sites' failed pages (RETRY_BEFORE), "
                                                       "in done months too")
    a = ap.parse_args()
    if a.what == "report":
        return report(a.db, a.year)
    run(a.what, a.start, a.end, a.db, set(a.outlets) if a.outlets else None,
        set(a.retry_hosts) if a.retry_hosts else None)


if __name__ == "__main__":
    main()
