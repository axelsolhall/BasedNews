"""Minimal Wayback Machine client (A24).

Since 2026-10-10 the CDX search API answers 403 ("requires authorization") and the availability API returns empty,
so we use the plain redirect: GET web.archive.org/web/<YYYYMMDD>id_/<url> → 302 to the nearest capture → raw bytes.
Polite by design: global ≈1 req/s, exponential backoff on 429/5xx/timeouts, ArchiveUnavailable after repeated failure.
"""
from __future__ import annotations

import datetime as dt
import re
import threading
import time
import urllib.error
import urllib.request

from pipeline.net import USER_AGENT, iri_to_uri

MIN_INTERVAL_S = 1.0
_lock, _last = threading.Lock(), [0.0]
_TS = re.compile(r"/web/(\d{14})id_/")


class ArchiveUnavailable(RuntimeError):
    """archive.org down/overloaded after retries. Callers should wait and retry, not mark work done."""


def _get(url: str, timeout: float = 60) -> tuple[bytes, str]:
    for attempt in range(6):
        with _lock:
            wait = _last[0] + MIN_INTERVAL_S - time.time()
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.time()
        try:
            req = urllib.request.Request(iri_to_uri(url), headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                # hard wall-clock deadline: a server dripping bytes slowly never trips the per-read timeout
                # (bug seen 2026-10-11: backfill hung >1.5 h inside one download)
                deadline, chunks = time.time() + 90, []
                while True:
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    if time.time() > deadline:
                        raise TimeoutError(f"download exceeded 90 s: {url}")
                return b"".join(chunks), r.geturl()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(min(300, 10 * 2 ** attempt))
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(min(120, 5 * 2 ** attempt))
    raise ArchiveUnavailable(f"gave up after retries: {url}")


def near(url: str, yyyymmdd: str, window_days: int = 45) -> tuple[str, bytes] | None:
    """(capture_timestamp, raw_bytes) of the capture nearest the date if within ±window_days, else None."""
    try:
        body, final = _get(f"https://web.archive.org/web/{yyyymmdd}id_/{url}")
    except urllib.error.HTTPError:
        return None
    m = _TS.search(final)
    if not m:
        return None
    ts = m.group(1)
    delta = abs((dt.datetime.strptime(ts[:8], "%Y%m%d") - dt.datetime.strptime(yyyymmdd, "%Y%m%d")).days)
    return (ts, body) if delta <= window_days else None
