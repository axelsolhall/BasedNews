"""H3 fetch: headline, description and publish time of history articles, from the publisher's own page (A61, A10).

Only the page head is read (≤ 256 kB): og:title / <title>, og:description / meta description, and the publish time
from article:published_time, JSON-LD datePublished or <time datetime>. That is headline + snippet — what A10 allows
anywhere — and the same shape live embeds for feed articles (title + RSS summary). robots.txt is respected per site;
each site gets one request at a time with ≥ 1 s between requests, many sites in parallel.

    fetch_many([(url, outlet, country, seen), ...]) → {url: Page | None}
"""
from __future__ import annotations

import html
import json
import re
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from pipeline import net
from pipeline.ingest.feeds import to_utc_iso

HEAD_BYTES = 256_000                  # read at least this much of a page
HEAD_MAX = 2_000_000                  # ... and on until its og:title (MTV's metadata sits at 290 kB, in the body)


def read_head(r) -> bytes:
    """A page's first HEAD_BYTES, continued until an og:title appears (at most HEAD_MAX)."""
    body = r.read(HEAD_BYTES)
    done = b"og:title" in body
    while not done and len(body) < HEAD_MAX:
        more = r.read(64_000)
        if not more:
            break
        done = b"og:title" in body[-8:] + more
        body += more
    return body


META = re.compile(r"<meta\s[^>]*>", re.I)
ATTR = re.compile(r'([\w:-]+)\s*=\s*("([^"]*)"|\'([^\']*)\')', re.I)
TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
JSONLD = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.I | re.S)
REFRESH = re.compile(r'<meta[^>]+http-equiv=["\']?refresh["\']?[^>]+content=["\'][^"\'>]*?url=([^"\'>]+)', re.I)
TIME = re.compile(r'<time[^>]+datetime="([^"]+)"', re.I)


@dataclass(frozen=True)
class Page:
    title: str
    summary: str | None
    published: str | None          # ISO UTC when the page says so


def _meta(page: str) -> dict[str, str]:
    out = {}
    for tag in META.findall(page):
        a = {m.group(1).lower(): html.unescape(m.group(3) if m.group(3) is not None else m.group(4)) for m in ATTR.finditer(tag)}
        key = a.get("property") or a.get("name") or a.get("itemprop")
        if key and "content" in a and key.lower() not in out:
            out[key.lower()] = a["content"].strip()
    return out


def _jsonld_date(page: str) -> str | None:
    for block in JSONLD.findall(page):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        for d in data if isinstance(data, list) else [data, *(data.get("@graph", []) if isinstance(data, dict) else [])]:
            if isinstance(d, dict) and d.get("datePublished"):
                return str(d["datePublished"])
    return None


def parse(page: str) -> Page | None:
    m = _meta(page)
    title = m.get("og:title") or m.get("twitter:title")
    if not title:
        t = TITLE.search(page)
        title = html.unescape(re.sub(r"\s+", " ", t.group(1))).strip() if t else None
    if not title:
        return None
    summary = m.get("og:description") or m.get("description") or m.get("twitter:description")
    raw = m.get("article:published_time") or m.get("datepublished") or _jsonld_date(page)
    if not raw:
        t = TIME.search(page)
        raw = t.group(1) if t else None
    return Page(title=title, summary=summary or None, published=to_utc_iso(raw) if raw else None)


def fetch_page(url: str, timeout: float = 8, hops: int = 1) -> Page | None:
    """A page's headline, description and date; a page that only says "go here" (an HTML meta refresh on the same
    site, as Delo's old addresses do) is followed once."""
    req = urllib.request.Request(net.iri_to_uri(url), headers={"User-Agent": net.USER_AGENT, "Accept": "text/html"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if "html" not in (r.headers.get("Content-Type") or "html"):
                return None
            final, body = r.geturl(), read_head(r).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 406, 429, 451) or e.code >= 500:
            _NET.failed = True                              # the site refuses us or struggles: not this page
        if e.code in (406, 429, 503):
            _NET.throttled = True                           # "slow down" (Bonnier answers 406 to a burst)
        return None
    except Exception:                                       # noqa: BLE001 — timeouts, resets, dead hosts
        _NET.failed = _NET.throttled = True
        return None
    page = parse(body)
    if hops and (page is None or not page.published):
        m = REFRESH.search(body)
        to = urljoin(final, html.unescape(m.group(1)).strip(" '\"")) if m else None
        if to and urlsplit(to).netloc.lower() == urlsplit(final).netloc.lower() and to != final:
            return fetch_page(to, timeout, hops - 1) or page
    return page


_NET = threading.local()                  # did the last fetch_page in this thread fail on the network / get told to slow down?


class _Site:
    """One site's pace: up to `c` requests in flight, starts at least gap/c seconds apart."""

    def __init__(self, c: float):
        self.c, self.inflight, self.next_at, self.streak, self.fails = c, 0, 0.0, 0, 0
        self.allowed: bool | None = None
        self.cond = threading.Condition()


class _Polite:
    """Each site politely: robots.txt checked once; requests at least gap/c seconds apart with up to c in flight.
    c starts at `start` and is adaptive (A75): one more after every RAISE_AFTER good answers, up to `most`; halved
    (at least 1) and the site paused PAUSE seconds when it says slow down (406, 429, 503) or stops answering.
    start = most = 1 is the plain one-at-a-time, one-per-gap pace."""
    RAISE_AFTER, PAUSE, MAX_WAIT = 20, 30.0, 600.0

    def __init__(self, gap: float = 1.0, give_up: int = 3, timeout: float = 8, start: int = 1, most: int = 1):
        self.gap, self.give_up, self.timeout, self.start, self.most = gap, give_up, timeout, start, most
        self.sites: dict[str, _Site] = {}
        self.guard = threading.Lock()
        self.skipped = None
        self.defer = False                     # fetch_many sets it: a busy site's pages go back for a later round

    def get(self, url: str) -> Page | None:
        host = urlsplit(url).netloc.lower()
        with self.guard:
            s = self.sites.setdefault(host, _Site(self.start))
        with s.cond:
            if s.allowed is None:
                s.allowed = net.robots_allows(url) is not False
            since = time.monotonic()
            while True:
                if time.monotonic() - since > self.MAX_WAIT:      # never hang a batch on one site (and say why)
                    print(f"  {host}: waited {self.MAX_WAIT / 60:.0f} min for a slot (in flight {s.inflight}, "
                          f"c {s.c}, next in {s.next_at - time.monotonic():.0f} s, fails {s.fails}) — skipped", flush=True)
                    return self.skipped
                if not s.allowed:
                    return None
                if s.fails >= self.give_up:
                    return self.skipped                          # one dead or slow site must not hold up the day
                now = time.monotonic()
                if s.inflight < int(s.c) and now >= s.next_at:
                    s.inflight += 1
                    s.next_at = now + self.gap / s.c
                    break
                if self.defer and s.inflight >= int(s.c):
                    return DEFER                                 # the site is busy: take another site's page instead
                s.cond.wait(timeout=max(0.05, s.next_at - now) if s.inflight < int(s.c) else 1.0)
        _NET.failed = _NET.throttled = False
        try:
            page = fetch_page(url, self.timeout)
        finally:
            with s.cond:
                s.inflight -= 1
                # only the site's failures count toward giving up on it — a page that answers without an article
                # (a 404, a radio page with no metadata) says nothing about the next one (DR: 25 such in a row)
                s.fails = s.fails + 1 if _NET.failed else 0
                if _NET.throttled:
                    s.c, s.streak = max(1.0, s.c / 2), 0
                    s.next_at = max(s.next_at, time.monotonic() + self.PAUSE)
                elif not _NET.failed:
                    s.streak += 1
                    if s.streak >= self.RAISE_AFTER and s.c < self.most:
                        s.c, s.streak = min(self.most, s.c + 1), 0
                s.cond.notify_all()
        return page


DEFER = "defer"                       # not asked yet: its site was busy (fetch_many asks again in the next round)
SKIPPED = "skipped"                   # not asked: the site was given up on in this call (fetch_many(skipped=SKIPPED))


def fetch_many(urls: list[str], workers: int = 64, gap: float = 1.0, give_up: int = 3,
               timeout: float = 8, skipped=None, start: int = 1, most: int = 1,
               polite: "_Polite | None" = None) -> dict[str, Page | None]:
    """Many sites in parallel, each site politely. Order the input by site round-robin for best throughput. give_up:
    consecutive failures after which a site is skipped for the rest of the call (the backfill raises it, A75); its
    remaining pages are `skipped` (None by default; the backfill passes SKIPPED so it does not record them as tried).
    start, most: requests in flight per site, adaptive between 1 and most (_Polite)."""
    polite = polite or _Polite(gap, give_up, timeout, start, most)           # one across batches keeps each site's pace
    polite.skipped = skipped
    by_host: dict[str, list[str]] = {}
    for u in urls:
        by_host.setdefault(urlsplit(u).netloc.lower(), []).append(u)
    order, i = [], 0
    while any(i < len(v) for v in by_host.values()):                    # interleave sites so no worker queues on one
        order += [v[i] for v in by_host.values() if i < len(v)]
        i += 1
    return dict(fetch_iter(order, workers, polite))


HEARTBEAT = 300                       # seconds between fetch_iter's progress lines


def fetch_iter(order: list[str], workers: int, polite: _Polite):
    """(url, page) as each page comes back, in any order. A worker never queues behind a busy site (hundreds of threads
    behind one slow site at one at a time idled the rest, 2026-10-20): it puts the page back at the end of the line
    and takes the next one."""
    import queue
    from collections import deque
    line, guard, done = deque(order), threading.Lock(), queue.Queue()

    def work():
        try:
            while True:
                with guard:
                    if not line:
                        break
                    u = line.popleft()
                try:
                    p = polite.get(u)
                except Exception as e:                  # noqa: BLE001 — a page's error must not end the worker:
                    print(f"  {u[:80]}: {type(e).__name__}: {str(e)[:80]}", flush=True)   # its sign-off never came
                    p = None                            # and the call waited forever (2026-10-20)
                if p is DEFER:
                    with guard:
                        line.append(u)
                    time.sleep(0.2)                     # 512 workers on a few busy sites: do not spin
                else:
                    done.put((u, p))
        finally:
            done.put(None)
    polite.defer = True
    n = min(workers, len(order)) or 1
    for _ in range(n):
        threading.Thread(target=work, daemon=True).start()
    left, got, beat = n, 0, time.monotonic()
    while left:
        try:
            item = done.get(timeout=60)
        except queue.Empty:
            item = False
        if time.monotonic() - beat > HEARTBEAT:          # say what the call waits on
            beat = time.monotonic()
            with guard:
                rest = Counter(urlsplit(u).netloc.lower() for u in line)
            now = time.monotonic()
            top = ", ".join(f"{h} {k} (in flight {polite.sites[h].inflight}, c {polite.sites[h].c:g}, "
                            f"next {polite.sites[h].next_at - now:.0f} s, fails {polite.sites[h].fails})"
                            for h, k in rest.most_common(5) if h in polite.sites)
            print(f"  fetch: {got:,} back, {sum(rest.values()):,} left, workers {left} · {top}", flush=True)
        if item is None:
            left -= 1
        elif item:
            got += 1
            yield item
    polite.defer = False
