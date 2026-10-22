"""H3 quality: fetched pages that are not articles (A76) — a bot wall, or a section/home page a dead link landed on.

    PYTHONPATH=. uv run python -m pipeline.history.quality   (eval: junk titles per outlet, and the rows they hold)

A history row is a GDELT (or sitemap) link plus the headline its page gave. When the link no longer leads to the
article, the page is often the site's front or section page ("bhrt", "Arvamus", "TV 2 Play") or a bot check
("Client Challenge", "Distinct Captcha"); the row then carries a headline that is not one. Two rules, no model:
  - WALL: a title that names a bot check or a block;
  - REPEAT: a title an outlet has given ≥ REPEAT times (measured 2026-10-20: every title at ≥ 30 was a section page,
    site title, wall, or a recurring rundown — 147 titles, 222,491 rows; Le Monde 74,999 "Client Challenge").
New pages are checked before they are stored (replay.store_pages); old rows are only counted here (tagging them is
the maintainer's call: data/history/junk_tag.sql).
"""
from __future__ import annotations

import re
from collections import Counter

REPEAT = 30                         # an outlet's title given this many times is not a headline
BATCH_REPEAT = 5                    # ... nor one a single fetch batch got this many times
WALL = re.compile(r"client challenge|challenge validation|captcha|just a moment|attention required|access denied|"
                  r"are you a robot|not available in your region|security check|verify you are human|"
                  r"enable javascript|request rejected|403 forbidden|page not found|404 not found|^\s*loading\W*$",
                  re.I)


def is_wall(title: str | None) -> bool:
    return bool(title and WALL.search(title))


def junk_titles(conn) -> set[tuple[str, str]]:
    """(outlet, title) pairs given ≥ REPEAT times, and walls already stored."""
    return {(o, t) for o, t, n in conn.execute(
        "SELECT outlet_id, title, count(*) FROM articles GROUP BY outlet_id, title HAVING count(*) >= ?", (REPEAT,))}


def kept(items: list[tuple[str, str, object]], junk: set[tuple[str, str]] | None = None) -> set[str]:
    """items: (url, outlet, page). The urls whose page is an article by both rules, not BATCH_REPEAT times the same
    title in this batch, and only the first of a title the batch got 2–4 times (one article under several addresses:
    CBS's local-station copies, mobile and www)."""
    n = Counter((o, p.title) for _, o, p in items if p)
    out, first = set(), set()
    for u, o, p in items:
        if not p or is_wall(p.title) or n[(o, p.title)] >= BATCH_REPEAT or (junk and (o, p.title) in junk):
            continue
        if (o, p.title) not in first:
            first.add((o, p.title))
            out.add(u)
    return out


if __name__ == "__main__":
    from pipeline import store
    from pipeline.history.replay import DB
    conn = store.connect(str(DB))
    rows = conn.execute("SELECT outlet_id, title, count(*) n FROM articles GROUP BY 1, 2 HAVING n >= ? "
                        "ORDER BY n DESC", (REPEAT,)).fetchall()
    walls = conn.execute("SELECT count(*) FROM articles WHERE title LIKE '%challenge%' OR title LIKE '%captcha%'"
                         ).fetchone()[0]
    print(f"{len(rows)} junk titles holding {sum(r[2] for r in rows):,} rows; wall-like titles {walls:,}")
    by = Counter()
    for o, t, n in rows:
        by[o] += n
    for o, n in by.most_common(30):
        print(f"  {o:28s} {n:7,d}  e.g. {next(t for oo, t, _ in rows if oo == o)[:60]}")
