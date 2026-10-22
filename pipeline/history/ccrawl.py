"""H3e archive: Common Crawl's copy of a page the publisher no longer serves (A75), read from its raw files.

    BASEDNEWS_IPV4=1 PYTHONPATH=. uv run python -m pipeline.history.ccrawl <url> <YYYY-MM-DD>   (eval: what it finds)

Common Crawl publishes each crawl's index as gzip blocks of CDX lines sorted by SURT key (com,example)/path), with a
small cluster.idx naming each block's first key, file, offset and length — all on data.commoncrawl.org, which serves
byte ranges. The index server (index.commoncrawl.org) timed out on us (504), so it is not used: a host's lines are
found by binary search over cluster.idx with range requests (≈ 15 of 8 kB), its blocks read once per crawl and kept
(data/history/ccrawl/<crawl>/<host>.json.gz: the host's 200 text/html captures), and a page is one range request into
the crawl's WARC file. The same rules as the Wayback copies: only for sites that neither refuse us nor disallow us
(backfill.site_gate, A33/A10), the copy must be of this address (no redirects: CDX status 200), and only crawls made
after GDELT saw the page (CC_AFTER_DAYS), nearest first.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

from pipeline import net

BASE = "https://data.commoncrawl.org/"
CACHE = Path("data/history/ccrawl")
CC_AFTER_DAYS = 365                  # crawls up to this long after GDELT's sighting (a page lives on; a later copy is
CC_CRAWLS = 3                        # still the article) — at most this many tried, nearest first
PROBE = 8192                         # bytes read per binary-search step
IN_FLIGHT = 4                        # requests to data.commoncrawl.org at a time (it answers 503 "slow down" above)
CRAWL = re.compile(r"CC-MAIN-(\d{4})-(\d{2})")
_slots = threading.BoundedSemaphore(IN_FLIGHT)
_pause = {"until": 0.0}
_blocked = threading.Event()         # CloudFront answered 403 "Request blocked": no more requests this run (A33)
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_crawls: list[tuple[dt.date, str]] = []
_sizes: dict[str, int] = {}


class Busy(Exception):
    """data.commoncrawl.org did not answer after retries, or blocks us: the caller records nothing (asked again
    later)."""


def _get(path: str, lo: int | None = None, hi: int | None = None, method: str = "GET"):
    headers = {"User-Agent": net.USER_AGENT}
    if lo is not None:
        headers["Range"] = f"bytes={lo}-{hi}"
    for attempt in range(6):
        time.sleep(max(0.0, _pause["until"] - time.monotonic()))
        if _blocked.is_set():
            raise Busy(path)
        with _slots:
            try:
                req = urllib.request.Request(BASE + path, headers=headers, method=method)
                with urllib.request.urlopen(req, timeout=60) as r:
                    return r.headers if method == "HEAD" else r.read()
            except urllib.error.HTTPError as e:
                if e.code == 416:                                   # past the end of the file
                    return b""
                if e.code == 403:                                   # blocked (2026-10-20, after ≈ 1 h at 4 in
                    if not _blocked.is_set():                       # flight): a refusal, not a missing copy
                        print("  data.commoncrawl.org blocks us (403): stopping", flush=True)
                    _blocked.set()
                    raise Busy(path) from e
                if e.code not in (429, 500, 502, 503, 504):
                    raise
            except Exception:                                       # noqa: BLE001 — timeouts, resets
                pass
        _pause["until"] = max(_pause["until"], time.monotonic() + 20 * (attempt + 1))
    raise Busy(path)


def crawls() -> list[tuple[dt.date, str]]:
    """(about when, crawl id) for every crawl, oldest first; the id's week is when it ran."""
    if not _crawls:
        page = _get("crawl-data/index.html").decode("utf-8", "replace")
        for cid in sorted(set(re.findall(r"CC-MAIN-\d{4}-\d{2}", page))):
            y, w = map(int, CRAWL.match(cid).groups())
            _crawls.append((dt.date.fromisocalendar(y, max(1, min(w, 52)), 1), cid))
    return _crawls


def after(seen: dt.datetime, n: int = CC_CRAWLS, days: int = CC_AFTER_DAYS) -> list[str]:
    """The crawls made in the days after a sighting, nearest first."""
    d = seen.date()
    return [cid for when, cid in crawls() if d <= when <= d + dt.timedelta(days=days)][:n]


def host_key(host: str) -> str:
    """SURT host part: www.example.co.uk → uk,co,example (Common Crawl drops a leading www)."""
    host = re.sub(r"^www\d*\.", "", host.lower().split(":")[0])
    return ",".join(reversed(host.split(".")))


def norm(url: str) -> str:
    """An address as the index's url field is matched: host without www, path without a trailing slash or a
    directory's index.html (adevarul.ro/…/x/ was adevarul.ro/…/x/index.html in 2019), query arguments sorted, no
    fragment."""
    u = urlsplit(url)
    host = re.sub(r"^www\d*\.", "", u.netloc.lower().split(":")[0])
    path = re.sub(r"/index\.html?$", "", unquote(u.path)).rstrip("/") or "/"
    q = "&".join(f"{k}={v}" for k, v in sorted(parse_qsl(u.query, keep_blank_values=True)))
    return f"{host}{path}{'?' + q if q else ''}"


def _idx(cid: str) -> str:
    return f"cc-index/collections/{cid}/indexes/cluster.idx"


def _key_after(cid: str, off: int) -> str | None:
    """The key of the first whole cluster.idx line after byte off."""
    raw = _get(_idx(cid), off, off + PROBE - 1)
    start = raw.find(b"\n") + 1
    end = raw.find(b"\n", start)
    if not start or end < 0:
        return None
    return raw[start:end].decode("utf-8", "replace").split(" ", 1)[0]


def _blocks(cid: str, prefix: str) -> list[tuple[str, int, int]]:
    """(cdx file, offset, length) of the index blocks that can hold keys starting with prefix: the last block whose
    first key sorts before prefix, and every block whose first key starts with it."""
    if cid not in _sizes:
        _sizes[cid] = int(_get(_idx(cid), method="HEAD")["Content-Length"])
    lo, hi = 0, _sizes[cid]                             # lo: a line before lo's next line sorts before prefix
    while hi - lo > PROBE:
        mid = (lo + hi) // 2
        key = _key_after(cid, mid)
        if key is None or key >= prefix:
            hi = mid
        else:
            lo = mid
    rows, off, buf, done = [], lo, b"", False
    while not done:                                     # read on from lo until keys pass the prefix
        chunk = _get(_idx(cid), off, off + 65535)
        if not chunk:
            break
        off += len(chunk)
        lines = (buf + chunk).split(b"\n")
        buf = lines.pop()
        if off - len(chunk) == lo and lo:               # the first line read starts mid-line
            lines = lines[1:]
        for ln in lines:
            parts = ln.decode("utf-8", "replace").split("\t")
            if len(parts) < 4:
                continue
            key = parts[0].split(" ", 1)[0]
            if key > prefix and not key.startswith(prefix):
                done = True
                break
            rows.append((key, parts[1], int(parts[2]), int(parts[3])))
    before = [k for k, r in enumerate(rows) if r[0] < prefix]
    keep = rows[before[-1]:] if before else rows
    return [(f, o, n) for _key, f, o, n in keep]


def host_index(cid: str, host: str) -> dict[str, list]:
    """norm(url) → [timestamp, warc file, offset, length] for the host's 200 text/html captures in the crawl (kept on
    disk: one host's blocks are read once per crawl)."""
    hk = host_key(host)
    path = CACHE / cid / f"{hk}.json.gz"
    with _locks_guard:
        lock = _locks.setdefault(f"{cid}/{hk}", threading.Lock())
    with lock:
        if path.exists():
            return json.loads(gzip.decompress(path.read_bytes()))
        prefix = hk + ")"
        out: dict[str, list] = {}
        for f, o, n in _blocks(cid, prefix):
            lines = zlib.decompress(_get(f"cc-index/collections/{cid}/indexes/{f}", o, o + n - 1), 16 + zlib.MAX_WBITS)
            for ln in lines.decode("utf-8", "replace").splitlines():
                key, _ts, js = ln.split(" ", 2)
                if not key.startswith(prefix):
                    continue
                d = json.loads(js)
                if d.get("status") != "200" or "html" not in (d.get("mime-detected") or d.get("mime") or ""):
                    continue
                out.setdefault(norm(d["url"]), [_ts, d["filename"], int(d["offset"]), int(d["length"])])
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{threading.get_ident()}.part")
        tmp.write_bytes(gzip.compress(json.dumps(out).encode()))
        tmp.rename(path)
        return out


def record_html(filename: str, offset: int, length: int) -> str:
    """The page body of one WARC response record (a gzip member at offset)."""
    raw = zlib.decompress(_get(filename, offset, offset + length - 1), 16 + zlib.MAX_WBITS)
    parts = raw.split(b"\r\n\r\n", 2)                   # WARC headers, HTTP headers, body
    body = parts[2] if len(parts) == 3 else b""
    m = re.search(rb"charset=[\"']?([\w-]+)", parts[1] if len(parts) > 1 else b"")
    try:
        return body.decode(m.group(1).decode() if m else "utf-8", errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def find(url: str, seen: dt.datetime):
    """(crawl, Page or None) for the nearest copy after the sighting, or None when no crawl has one."""
    from pipeline.history.fetch import parse
    host, key = urlsplit(url).netloc, norm(url)
    for cid in after(seen):
        hit = host_index(cid, host).get(key)
        if hit:
            return cid, parse(record_html(*hit[1:]))
    return None


if __name__ == "__main__":
    url, day = sys.argv[1], dt.datetime.fromisoformat(sys.argv[2])
    print("crawls after:", after(day))
    t0 = time.monotonic()
    for cid in after(day):
        idx = host_index(cid, urlsplit(url).netloc)
        print(f"  {cid}: {len(idx):,} html captures of {urlsplit(url).netloc} ({time.monotonic() - t0:.0f} s)")
    got = find(url, day)
    print("found:", got[0] if got else None)
    if got and got[1]:
        print(" ", got[1].published, got[1].title, "|", (got[1].summary or "")[:100])
