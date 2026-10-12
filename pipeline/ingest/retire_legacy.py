"""Retire the pre-pipeline scraper's articles from the dataset (A37). Reversible.

    uv run python -m pipeline.ingest.retire_legacy [--dry-run | --restore]

The old scraper (ingested 2025-12-23 .. 2026-01-11) read Nordic outlets only, densely, for three weeks. History is
meant to be a sparse sample on the same dates for every outlet (A23), which the archive.org backfill (1.3) provides
for that period too, so these rows would make five countries' history ~100x denser than the rest in one window.
They move to `articles_legacy` (same columns); their headlines and vectors are dropped (recomputable on restore).
"""
from __future__ import annotations

import argparse

from pipeline import store
from pipeline.ingest.eval import LIVE_SINCE

LEGACY = f"""text_source NOT LIKE 'wayback-%' AND NOT (ingested_at LIKE '____-__-__T%' AND ingested_at >= '{LIVE_SINCE}')"""


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--restore", action="store_true", help="move the retired rows back into `articles`")
    args = ap.parse_args()
    conn = store.connect()
    conn.execute("CREATE TABLE IF NOT EXISTS articles_legacy AS SELECT * FROM articles WHERE 0")
    if args.restore:
        n = conn.execute("INSERT OR IGNORE INTO articles SELECT * FROM articles_legacy").rowcount
        conn.execute("DELETE FROM articles_legacy")
        store.seed_headlines(conn)
        conn.commit()
        print(f"restored {n} articles; run pipeline.normalize.run to rebuild their headline histories")
        return
    ids = [r[0] for r in conn.execute(f"SELECT id FROM articles WHERE {LEGACY}")]
    per = dict(conn.execute(f"SELECT outlet_id, COUNT(*) FROM articles WHERE {LEGACY} GROUP BY 1 ORDER BY 2 DESC").fetchall())
    if args.dry_run:
        print(f"would retire {len(ids)} articles: {per}")
        return
    conn.execute(f"INSERT OR IGNORE INTO articles_legacy SELECT * FROM articles WHERE {LEGACY}")
    conn.executemany("DELETE FROM headlines WHERE article_id = ?", [(i,) for i in ids])
    conn.executemany("DELETE FROM embeddings WHERE article_id = ?", [(i,) for i in ids])
    conn.execute(f"DELETE FROM articles WHERE {LEGACY}")
    conn.commit()
    print(f"retired {len(ids)} articles to articles_legacy: {per}")


if __name__ == "__main__":
    main()
