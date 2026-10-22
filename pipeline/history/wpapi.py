"""H3d discovery: an outlet's articles of a month from its own WordPress posts API (A75).

    BASEDNEWS_IPV4=1 PYTHONPATH=. uv run python -m pipeline.history.wpapi <outlet> 2019-03   (eval: what it finds)

WordPress sites answer /wp-json/wp/v2/posts?after=…&before=… with each post's address, publish time, title and
excerpt — the same three things the page fetch reads from a page's head, published by the site for machines, 100
posts a request. No page is fetched. Same politeness as the page fetch: robots.txt, our user agent, ≥ 1 s per site.
Probe 2026-10-20 (all 228 outlets): 10 answer — Newsbook, Ziarul de Gardă, Nova Makedonija, Telma, RTSH (from 2020),
Euronews Albania (from 2019), News247, in.gr, OKDiario, TVM (0 posts).
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from pipeline import net
from pipeline.history.fetch import Page

FIELDS = "date_gmt,link,title,excerpt"
PER_PAGE = 100
SUMMARY_CHARS = 400                  # an excerpt is cut to about an og:description's length
TAG = re.compile(r"<[^>]+>")
_last: dict[str, float] = {}
_lock = threading.Lock()


def _get(url: str):
    host = urlsplit(url).netloc.lower()
    with _lock:
        wait = 1.0 - (time.monotonic() - _last.get(host, 0.0))
        _last[host] = time.monotonic() + max(wait, 0)
    if wait > 0:
        time.sleep(wait)
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": net.USER_AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read()), r.headers
        except urllib.error.HTTPError as e:
            if e.code == 400:                                   # past the last page
                return [], {}
            if e.code in (429, 503):
                time.sleep(30 * (attempt + 1))
                continue
            return None, {}
        except Exception:                                       # noqa: BLE001
            time.sleep(10 * (attempt + 1))
    return None, {}


def text(s: str | None) -> str:
    return html.unescape(TAG.sub(" ", s or "")).split("Read more")[0].strip()


def month_pages(homepage: str, y: int, m: int) -> dict[str, Page]:
    """address → Page(title, excerpt, publish time) for the posts published in the month."""
    u = urlsplit(homepage)
    api = f"{u.scheme}://{u.netloc}/wp-json/wp/v2/posts"
    if net.robots_allows(api) is False:
        return {}
    lo = dt.datetime(y, m, 1)
    hi = dt.datetime(y + (m == 12), m % 12 + 1, 1)
    out: dict[str, Page] = {}
    page = 1
    while True:
        q = (f"{api}?after={lo.isoformat()}&before={hi.isoformat()}&per_page={PER_PAGE}&page={page}"
             f"&orderby=date&order=asc&_fields={FIELDS}")
        posts, headers = _get(q)
        if not posts:
            break
        for p in posts:
            title = text((p.get("title") or {}).get("rendered"))
            if not title or not p.get("link") or not p.get("date_gmt"):
                continue
            summary = re.sub(r"\s+", " ", text((p.get("excerpt") or {}).get("rendered")))[:SUMMARY_CHARS] or None
            out[p["link"]] = Page(title=title, summary=summary, published=p["date_gmt"][:19] + "Z")
        pages = int(headers.get("X-WP-TotalPages") or 0)
        if len(posts) < PER_PAGE or (pages and page >= pages):
            break
        page += 1
    return out


if __name__ == "__main__":
    from pipeline.sources.registry import load_outlets
    oid, ym = sys.argv[1], sys.argv[2]
    o = next(x for x in load_outlets() if x.id == oid)
    got = month_pages(o.homepage, int(ym[:4]), int(ym[5:7]))
    print(f"{oid} {ym}: {len(got):,} posts")
    for url, p in list(got.items())[:5]:
        print(" ", p.published, p.title[:70], "|", (p.summary or "")[:60], "|", url[:70])
