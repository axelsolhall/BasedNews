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
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import urlsplit

from pipeline import net
from pipeline.ingest.feeds import to_utc_iso

HEAD_BYTES = 256_000
META = re.compile(r"<meta\s[^>]*>", re.I)
ATTR = re.compile(r'([\w:-]+)\s*=\s*("([^"]*)"|\'([^\']*)\')', re.I)
TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
JSONLD = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.I | re.S)
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


def fetch_page(url: str, timeout: float = 8) -> Page | None:
    req = urllib.request.Request(net.iri_to_uri(url), headers={"User-Agent": net.USER_AGENT, "Accept": "text/html"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if "html" not in (r.headers.get("Content-Type") or "html"):
                return None
            body = r.read(HEAD_BYTES)
    except Exception:
        return None
    return parse(body.decode("utf-8", errors="replace"))


class _Polite:
    """One request at a time per site, at least `gap` seconds apart; robots.txt checked once per site."""

    def __init__(self, gap: float = 1.0, give_up: int = 3):
        self.gap, self.locks, self.last, self.allowed, self.guard = gap, {}, {}, {}, threading.Lock()
        self.give_up, self.fails = give_up, {}                 # consecutive failures per site; at give_up, skip the site

    def get(self, url: str) -> Page | None:
        host = urlsplit(url).netloc.lower()
        with self.guard:
            lock = self.locks.setdefault(host, threading.Lock())
        with lock:
            if host not in self.allowed:
                self.allowed[host] = net.robots_allows(url) is not False
            if not self.allowed[host] or self.fails.get(host, 0) >= self.give_up:
                return None                                      # one dead or slow site must not hold up the day
            wait = self.gap - (time.monotonic() - self.last.get(host, 0.0))
            if wait > 0:
                time.sleep(wait)
            try:
                page = fetch_page(url)
                self.fails[host] = 0 if page else self.fails.get(host, 0) + 1
                return page
            finally:
                self.last[host] = time.monotonic()


def fetch_many(urls: list[str], workers: int = 64, gap: float = 1.0) -> dict[str, Page | None]:
    """Many sites in parallel, each site politely. Order the input by site round-robin for best throughput."""
    polite = _Polite(gap)
    by_host: dict[str, list[str]] = {}
    for u in urls:
        by_host.setdefault(urlsplit(u).netloc.lower(), []).append(u)
    order, i = [], 0
    while any(i < len(v) for v in by_host.values()):                    # interleave sites so no worker queues on one
        order += [v[i] for v in by_host.values() if i < len(v)]
        i += 1
    with ThreadPoolExecutor(workers) as pool:
        return dict(zip(order, pool.map(polite.get, order)))
