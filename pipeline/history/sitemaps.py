"""H3c discovery: an outlet's articles of a month from its own sitemaps, where GDELT has none or few (A75).

    BASEDNEWS_IPV4=1 PYTHONPATH=. uv run python -m pipeline.history.sitemaps <outlet> 2019-03   (eval: what it finds)

Many news sites publish an archive of sitemaps by month or year (robots.txt "Sitemap:" lines → a sitemap index → one
sitemap per period → <url><loc>…</loc><lastmod>…</lastmod></url>). The index tree is read once per outlet (≤ 3 levels)
and cached in data/history/sitemaps/<outlet>.tree.json (with the entries of the leaves
that name no month, read once); a month's articles are the entries of the child sitemaps that name
that month (or only its year, or nothing), dated by news:publication_date or lastmod within the month. An undated
entry is left out: its day is unknown. Same politeness as the page fetch: robots.txt, our user agent, ≥ 1 s per site.
"""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import re
import sys
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from pipeline import net

CACHE = Path("data/history/sitemaps")
LOC = re.compile(r"<loc>\s*(?:<!\[CDATA\[)?\s*([^<\]]+?)\s*(?:\]\]>)?\s*</loc>", re.I)
ENTRY = re.compile(r"<url>(.*?)</url>", re.I | re.S)
DATE = re.compile(r"<(?:news:publication_date|lastmod)>\s*([^<]+?)\s*</", re.I)
# a period named in a child sitemap's address: 2019-03, 2019/03, 201903, 2019_03, year=2019&month=3, 2019-03-14
YM = [re.compile(r"(?<!\d)((?:19[89]|20[0-2])\d)[-/_.]?(0[1-9]|1[0-2])(?!\d)"),          # 1980–2029: HS dates from 1990
      re.compile(r"year=(20[12]\d)\D+month=(\d{1,2})(?!\d)", re.I)]
Y = re.compile(r"(?<!\d)((?:19[89]|20[0-2])\d)(?!\d)")
MAP = re.compile(r"\.xml(\.gz)?($|\?)|sitemap", re.I)
FALLBACK = ["/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml", "/wp-sitemap.xml"]   # robots names none
MAX_BYTES = 40_000_000
MAX_SITEMAPS = 20_000                 # an outlet's sitemaps listed at most (HS: one a day since 1990)
_last: dict[str, float] = {}
_lock = threading.Lock()


def get(url: str) -> str | None:
    """A sitemap's text (gzip or not), politely; None when robots.txt disallows it or it cannot be read."""
    host = urlsplit(url).netloc.lower()
    if net.robots_allows(url) is False:
        return None
    with _lock:
        wait = 1.0 - (time.monotonic() - _last.get(host, 0.0))
        _last[host] = time.monotonic() + max(wait, 0)
    if wait > 0:
        time.sleep(wait)
    try:
        req = urllib.request.Request(net.iri_to_uri(url), headers={"User-Agent": net.USER_AGENT})
        with urllib.request.urlopen(req, timeout=40) as r:
            data = r.read(MAX_BYTES)
    except Exception:                                                   # noqa: BLE001
        return None
    if data[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(data)
        except OSError:
            return None
    return data.decode("utf-8", errors="replace")


def period(url: str) -> tuple[int | None, int | None]:
    for rx in YM:
        m = rx.search(url)
        if m:
            return int(m.group(1)), int(m.group(2))
    m = Y.search(url)
    return (int(m.group(1)), None) if m else (None, None)


def tree(outlet: str, homepage: str) -> list[dict]:
    """Every leaf sitemap of the outlet with the period its address names (cached)."""
    path = CACHE / f"{outlet}.tree.json"
    if path.exists():
        return json.loads(path.read_text())
    u = urlsplit(homepage)
    root = f"{u.scheme}://{u.netloc}"
    robots = get(root + "/robots.txt") or ""
    todo = [(m, 0) for m in re.findall(r"(?im)^\s*sitemap\s*:\s*(\S+)", robots)] or [(root + p, 0) for p in FALLBACK]
    seen, leaves = set(), []
    while todo:
        url, depth = todo.pop()
        y, m = period(url)
        if url in seen or (depth and y and y < 2015):          # before the history: not even counted
            continue
        if len(seen) > MAX_SITEMAPS:
            continue
        seen.add(url)
        if depth and y and m:                       # a sitemap named by its month is a leaf: no need to read it now
            leaves.append({"url": url, "year": y, "month": m})
            continue
        text = get(url)
        if not text or "<loc" not in text.lower():
            continue
        locs = LOC.findall(text)
        maps = [c for c in locs if MAP.search(c)]
        # an index, or a urlset that lists sitemaps (some sites publish their index so)
        if depth < 3 and ("<sitemapindex" in text[:3000].lower() or (locs and len(maps) > len(locs) / 2)):
            todo += [(c, depth + 1) for c in maps or locs]
        else:
            leaves.append({"url": url, "year": y, "month": m})
            if m is None:
                _keep(outlet, url, text)
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(leaves))
    return leaves


def _parse(text: str) -> list[tuple[str, str]]:
    out = []
    for e in ENTRY.findall(text):
        loc, d = LOC.search(e), DATE.search(e)
        if loc and d:
            out.append((loc.group(1).strip(), d.group(1).strip()))
    return out


def _kept(outlet: str, url: str) -> Path:
    return CACHE / outlet / f"{hashlib.sha1(url.encode()).hexdigest()[:16]}.json.gz"


def _keep(outlet: str, url: str, text: str) -> list[tuple[str, str]]:
    out = _parse(text)
    path = _kept(outlet, url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(json.dumps(out).encode()))
    return out


def entries(outlet: str, leaf: dict) -> list[tuple[str, str]]:
    """A leaf's (address, date) entries; a leaf that names no month is read once and kept (it would be read for every
    month otherwise)."""
    if leaf["month"] is None and _kept(outlet, leaf["url"]).exists():
        return [tuple(e) for e in json.loads(gzip.decompress(_kept(outlet, leaf["url"]).read_bytes()))]
    text = get(leaf["url"])
    if not text:
        return []
    return _keep(outlet, leaf["url"], text) if leaf["month"] is None else _parse(text)


def month_urls(outlet: str, homepage: str, y: int, m: int) -> list[tuple[str, dt.datetime]]:
    """(article url, its date) for the month, from the leaves that can hold it."""
    lo = dt.datetime(y, m, 1, tzinfo=dt.timezone.utc)
    hi = dt.datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=dt.timezone.utc)
    out = {}
    for leaf in tree(outlet, homepage):
        if leaf["year"] not in (None, y) or leaf["month"] not in (None, m):
            continue
        for loc, d in entries(outlet, leaf):
            try:
                when = dt.datetime.fromisoformat(d.replace("Z", "+00:00"))
            except ValueError:
                continue
            when = when if when.tzinfo else when.replace(tzinfo=dt.timezone.utc)
            if lo <= when < hi:
                out.setdefault(loc, when)
    return sorted(out.items(), key=lambda x: x[1])


if __name__ == "__main__":
    from pipeline.sources.registry import load_outlets
    oid, ym = sys.argv[1], sys.argv[2]
    o = next(x for x in load_outlets() if x.id == oid)
    got = month_urls(o.id, o.homepage, int(ym[:4]), int(ym[5:7]))
    print(f"{oid} {ym}: {len(got):,} dated articles in its sitemaps; leaves {len(tree(o.id, o.homepage)):,}")
    for u, d in got[:5]:
        print(" ", d.isoformat(), u)
