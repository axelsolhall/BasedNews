"""M2 run: fetch feeds for usable outlets -> new articles in the store.

    uv run python -m pipeline.ingest.run [--only ID ...] [--no-text]
Uses M1's latest FeedHealth: skips outlets with no working feed; fetches full text only where allowed (A10).
Dedupe happens BEFORE downloading article pages (the old script downloaded everything first).
"""
from __future__ import annotations

import argparse
import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pipeline import net, store
from pipeline.contracts import Article, HeadlineVersion, Outlet
from pipeline.ingest.extract import clean_text, fetch_text
from pipeline.ingest.feeds import parse_feed, to_utc_iso, utc_now_iso
from pipeline.sources.registry import load_outlets

MIN_TEXT_CHARS = 300   # A19: below this we treat extraction as failed and fall back to the summary


def article_id(url: str, title: str) -> str:
    return hashlib.sha256(f"{url}|{title}".encode()).hexdigest()


def known_urls(urls: list[str], db=None) -> dict[str, tuple[str, set[str]]]:
    """The stored articles among these URLs: url -> (canonical article id, headlines seen so far, lower-case).
    Only these URLs are read (A63: loading every stored URL grows by ≈ 18 MB a day). A read-only connection of its
    own, so outlets can look up in parallel while the main thread writes (WAL)."""
    conn = sqlite3.connect(Path(db or store.default_db()).resolve().as_uri() + "?mode=ro", uri=True, timeout=120)
    out: dict[str, tuple[str, set[str]]] = {}
    try:
        uniq = list(dict.fromkeys(urls))
        for k in range(0, len(uniq), 900):                  # SQLite's parameter limit
            chunk = uniq[k:k + 900]
            for url, cid, t in conn.execute(
                    f"""SELECT a.url, coalesce(a.canonical_id, a.id), lower(h.title) FROM articles a
                        LEFT JOIN headlines h ON h.article_id = coalesce(a.canonical_id, a.id)
                        WHERE a.url IN ({','.join('?' * len(chunk))})""", chunk):
                out.setdefault(url, (cid, set()))[1].add(t or "")
    finally:
        conn.close()
    return out


def ingest_outlet(o: Outlet, allow_text: bool, lookup=known_urls
                  ) -> tuple[list[Article], list[str], list[HeadlineVersion]]:
    """New articles, feed errors, and headline edits: a known URL whose feed title changed (A34, no download).
    `lookup(urls)` returns the stored ones among this outlet's feed URLs (`known_urls`)."""
    new, errors, seen, edits = [], [], set(), []
    fetched = []
    for feed in o.feeds:
        try:
            fetched.append((feed, parse_feed(net.fetch(feed)[0])))
        except Exception as e:
            errors.append(f"{type(e).__name__}: {feed}")
    known = lookup([(it.get("url") or "").strip() for _, items in fetched for it in items if it.get("url")])
    for feed, items in fetched:
        for it in items:
            url, title = (it.get("url") or "").strip(), clean_text(it.get("title")) or ""
            if not url or not title or url in seen:
                continue
            if url in known:
                art_id, titles = known[url]
                if title.lower() not in titles:
                    edits.append(HeadlineVersion(art_id, title, utc_now_iso(), url, "feed_edit"))
                    titles.add(title.lower())
                continue
            seen.add(url)
            text, source = (fetch_text(url) if allow_text else (None, "none"))
            if not text or len(text) < MIN_TEXT_CHARS:
                text, source = None, "summary" if it.get("summary") else "none"
            new.append(Article(
                id=article_id(url, title), outlet_id=o.id, country=o.country, url=url, title=title,
                published=to_utc_iso(it.get("published")) or utc_now_iso(),   # A39: undated → first seen
                summary=clean_text(it.get("summary")),
                full_text=text, feed_url=feed, ingested_at=utc_now_iso(), text_source=source,
            ))
    return new, errors, edits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--no-text", action="store_true", help="metadata only (fast; for testing)")
    args = ap.parse_args()
    conn = store.connect()
    health = store.latest_health(conn)
    outlets = [o for o in load_outlets() if (not args.only or o.id in args.only)
               and o.id in health and health[o.id].feeds_ok > 0]

    def job(o):
        allow = health[o.id].full_text_allowed and not args.no_text
        return o, *ingest_outlet(o, allow)

    started, total, n_edits, errs = utc_now_iso(), 0, 0, {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        for o, arts, e, edits in pool.map(job, outlets):
            total += sum(store.insert_article(conn, a) for a in arts)
            n_edits += sum(store.add_headline(conn, h) for h in edits)
            conn.commit()                                   # short write transactions (backfill runs concurrently)
            if e:
                errs[o.id] = e
            print(f"{o.id:<16} +{len(arts)}" + (f"  ({len(edits)} headline edits)" if edits else ""))
    store.log_run(conn, "ingest", started, {"new_articles": total, "headline_edits": n_edits, "errors": errs})
    conn.commit()
    print(f"\n{total} new articles, {n_edits} headline edits on known URLs. Run `python -m pipeline.ingest.eval`.")


if __name__ == "__main__":
    main()
