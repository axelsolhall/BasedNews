"""Shared HTTP helpers: one User-Agent, timeouts, robots.txt and TDM opt-out checks (A10)."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import urllib.robotparser
from functools import lru_cache
from urllib.parse import quote, urlparse, urlsplit, urlunsplit

USER_AGENT = "BasedNewsBot/0.2 (+https://github.com/axelsolhall/BasedNews)"

# BASEDNEWS_IPV4=1: resolve to IPv4 only. On a network whose IPv6 route is broken (seen on the maintainer's home
# connection, 2026-10-14) every request otherwise waits ~30 s for the IPv6 attempt to time out — 35 s vs 3.4 s for one
# archive query. Off by default; production is unaffected.
if os.environ.get("BASEDNEWS_IPV4") == "1":
    import socket

    _getaddrinfo = socket.getaddrinfo
    socket.getaddrinfo = lambda *a, **k: [r for r in _getaddrinfo(*a, **k) if r[0] == socket.AF_INET]


def iri_to_uri(url: str) -> str:
    """Percent-encode non-ASCII characters (e.g. 'ü' in an archived Italian URL); ASCII URLs are unchanged."""
    if url.isascii():
        return url
    p = urlsplit(url)
    host = p.hostname.encode("idna").decode() + (f":{p.port}" if p.port else "") if p.hostname else p.netloc
    q = lambda v: quote(v, safe="/:@!$&'()*+,;=?%~-._")
    return urlunsplit((p.scheme, host, q(p.path), q(p.query), q(p.fragment)))


def fetch(url: str, timeout: float = 20) -> tuple[bytes, dict[str, str]]:
    req = urllib.request.Request(iri_to_uri(url), headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(), {k.lower(): v for k, v in resp.headers.items()}


@lru_cache(maxsize=512)
def robots_for(origin: str) -> urllib.robotparser.RobotFileParser | None:
    rp = urllib.robotparser.RobotFileParser()
    try:
        body, _ = fetch(origin + "/robots.txt", timeout=10)
        rp.parse(body.decode("utf-8", errors="ignore").splitlines())
        return rp
    except Exception:
        return None


def robots_allows(url: str) -> bool | None:
    """True/False from robots.txt for our UA; None if robots.txt could not be read."""
    u = urlparse(url)
    rp = robots_for(f"{u.scheme}://{u.netloc}")
    if rp is None:
        return None
    return rp.can_fetch(USER_AGENT, url)


@lru_cache(maxsize=512)
def tdm_reserved_for_origin(origin: str) -> bool | None:
    """W3C TDMRep: /.well-known/tdmrep.json with tdm-reservation = 1 means opted out. None = no policy file."""
    try:
        body, _ = fetch(origin + "/.well-known/tdmrep.json", timeout=10)
        data = json.loads(body.decode("utf-8", errors="ignore"))
        rules = data if isinstance(data, list) else [data]
        return any(int(r.get("tdm-reservation", 0)) == 1 for r in rules if isinstance(r, dict))
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError, TimeoutError, OSError):
        return None


def tdm_reserved_from_headers(headers: dict[str, str]) -> bool | None:
    v = headers.get("tdm-reservation")
    return None if v is None else v.strip() == "1"
