"""M1 discover: find working news feeds for reference outlets that have only a homepage (the 41-country list, A61).

    BASEDNEWS_IPV4=1 PYTHONPATH=. uv run python -m pipeline.sources.discover [--only ID ...]
    → data/feed_discovery.json (resumable) and one line per outlet: feeds found, items, newest age

Candidates: <link rel="alternate"> RSS/Atom links on the homepage, then common feed paths. A feed counts when it parses,
has ≥ 5 items and its newest item is < 48 h old (stale or empty feeds are what A29 rule 6 is about). Up to two feeds
are kept per outlet, preferring "top"/"latest"/"news" feeds, then fresher and fuller ones. Outlets with none get
`homepage` mode later (ingest reads article links from the front page instead of a feed), so the reference list
stays as it is and live and history measure the same outlets.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from pipeline import net
from pipeline.ingest.feeds import parse_feed, to_utc_iso

OUT = Path("data/feed_discovery.json")
PATHS = ["/rss", "/feed", "/rss.xml", "/feed.xml", "/feeds/rss", "/rss/news", "/index.xml", "/?feed=rss2", "/rss/all",
         "/arc/outboundfeeds/rss/", "/feed/", "/rss/", "/rssfeed", "/rss/latest", "/news/rss.xml", "/feeds/news.xml"]
LINK = re.compile(r"<link[^>]+>", re.I)
PREFER = re.compile(r"top|latest|siste|senaste|news|nyheter|nieuws|nachrichten|noticias|notizie|actu|uutiset|wiadomo|zpravy|spravy|hirek|vijesti|stiri|novini", re.I)
LOCK = threading.Lock()


def feed_links(home: str, html: str) -> list[str]:
    out = []
    for tag in LINK.findall(html):
        if re.search(r"rel=[\"']?alternate", tag, re.I) and re.search(r"(rss|atom)\+xml", tag, re.I):
            m = re.search(r"href=[\"']([^\"']+)", tag, re.I)
            if m:
                out.append(urljoin(home, m.group(1).replace("&amp;", "&")))
    return out


def check(url: str) -> dict | None:
    try:
        body, _ = net.fetch(url, timeout=15)
        items = parse_feed(body)
    except Exception:
        return None
    dates = [d for d in (to_utc_iso(i.get("published")) for i in items) if d]
    if len(items) < 5 or not dates:
        return None
    newest = max(dates)
    age_h = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(newest.replace("Z", "+00:00"))).total_seconds() / 3600
    if age_h > 48:
        return None
    return {"url": url, "items": len(items), "age_h": round(age_h, 1)}


def discover(o: dict) -> dict:
    home = o["homepage"]
    tried, found = set(), []
    try:
        html = net.fetch(home, timeout=20)[0].decode("utf-8", "replace")
        cands = feed_links(home, html)
    except Exception as e:
        cands, html = [], None
    root = f"{urlsplit(home).scheme}://{urlsplit(home).netloc}"
    cands += [root + p for p in PATHS] + [home.rstrip("/") + p for p in PATHS if urlsplit(home).path.strip("/")]
    for c in cands:
        if c in tried or len(found) >= 4:
            continue
        tried.add(c)
        r = check(c)
        if r and all(r["url"] != f["url"] for f in found):
            found.append(r)
    found.sort(key=lambda f: (not PREFER.search(f["url"]), f["age_h"], -f["items"]))
    return {"name": o["name"], "country": o["country"], "homepage": home, "homepage_ok": html is not None,
            "feeds": found[:2], "robots_ok": net.robots_allows(home)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()
    from pipeline.sources.registry import load_outlets
    live = {o.id for o in load_outlets()}
    outlets = [o for o in json.loads(Path("data/history_outlets.json").read_text())["outlets"]
               if (o["id"] in args.only if args.only else o["id"] not in live)]
    done = json.loads(OUT.read_text()) if OUT.exists() else {}
    todo = [o for o in outlets if args.only or o["id"] not in done]

    def run(o):
        r = discover(o)
        with LOCK:
            done[o["id"]] = r
            OUT.write_text(json.dumps(done, indent=1, ensure_ascii=False))
            f = r["feeds"]
            print(f"{o['id']:<30} {'OK ' if f else '-- '} {len(f)} feed(s)  " +
                  (f"{f[0]['items']} items, newest {f[0]['age_h']} h  {f[0]['url'][:70]}" if f else
                   ("homepage unreachable" if not r["homepage_ok"] else "no working feed")), flush=True)

    with ThreadPoolExecutor(8) as pool:          # different publishers in parallel; one outlet's requests in series
        list(pool.map(run, todo))
    ok = sum(bool(v["feeds"]) for v in done.values())
    print(f"\n{ok}/{len(done)} outlets have a working feed; the rest go to homepage mode")


if __name__ == "__main__":
    main()
