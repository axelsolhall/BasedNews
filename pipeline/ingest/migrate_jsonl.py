"""One-off: import the pre-pipeline JSONL archive (data/raw/<Country>/<outlet>.jsonl) into the store."""
from __future__ import annotations

import json
from pathlib import Path

from pipeline import store
from pipeline.contracts import Article
from pipeline.ingest.feeds import to_utc_iso
from pipeline.ingest.run import MIN_TEXT_CHARS


def main():
    conn = store.connect()
    added = 0
    for path in sorted(Path("data/raw").rglob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            text = r.get("full_text")
            ok = bool(text) and len(text) >= MIN_TEXT_CHARS
            added += store.insert_article(conn, Article(
                id=r["id"], outlet_id=r["outlet_id"], country=r["country"], url=r["url"], title=r.get("title") or "",
                published=to_utc_iso(r.get("published")) or r.get("published"), summary=r.get("summary"),
                full_text=text if ok else None, feed_url=r.get("feed_url", ""), ingested_at=r.get("ingested_at", ""),
                text_source="legacy" if ok else ("summary" if r.get("summary") else "none"),
            ))
    conn.commit()
    print(f"imported {added} legacy articles")


if __name__ == "__main__":
    main()
