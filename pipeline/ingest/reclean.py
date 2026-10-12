"""Re-apply the current text cleaning (extract.clean_text) to stored titles, summaries, texts and headline versions.

    uv run python -m pipeline.ingest.reclean [--dry-run]

Run after changing clean_text. Idempotent. Changed titles/summaries change M4's input hash, so 2.2 re-embeds them.
"""
from __future__ import annotations

import argparse

from pipeline import store
from pipeline.ingest.extract import clean_text, is_paywalled
from pipeline.ingest.run import MIN_TEXT_CHARS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    conn = store.connect()
    changes, n = [], {"title": 0, "summary": 0, "full_text": 0}
    paywalled = []
    for r in conn.execute("SELECT id, title, summary, full_text FROM articles"):
        if r["full_text"] and is_paywalled(r["full_text"]):
            paywalled.append(r["id"])
            continue
        new = {k: (clean_text(r[k]) if r[k] else r[k]) for k in n}
        if new["title"] is None and r["title"]:
            new["title"] = r["title"]                    # never blank a headline
        if new["full_text"] and len(new["full_text"]) < MIN_TEXT_CHARS:
            new["full_text"] = None                      # nothing left once the notice is gone (A19)
        diff = {k: v for k, v in new.items() if v != r[k]}
        for k in diff:
            n[k] += 1
        if diff:
            changes.append((r["id"], diff))
    heads = [(clean_text(h["title"]) or h["title"], h["article_id"], h["title"])
             for h in conn.execute("SELECT article_id, title FROM headlines")]
    heads = [h for h in heads if h[0] != h[2]]
    print(f"{'would change' if args.dry_run else 'changed'}: {n} in articles, {len(heads)} headline versions; "
          f"paywalled texts dropped: {len(paywalled)}")
    if args.dry_run:
        for i, diff in changes[:3]:
            print("  e.g.", {k: (v or "")[:90] for k, v in diff.items()})
        return
    conn.execute("""CREATE TABLE IF NOT EXISTS articles_shared_text (
        article_id TEXT PRIMARY KEY, full_text TEXT, text_source TEXT, cleaned_at TEXT)""")   # reversible backup
    conn.executemany("INSERT OR REPLACE INTO articles_shared_text SELECT id, full_text, text_source, datetime('now') "
                     "FROM articles WHERE id = ?", [(i,) for i in paywalled])
    conn.executemany("UPDATE articles SET full_text = NULL, text_source = text_source || '+paywall' WHERE id = ?",
                     [(i,) for i in paywalled])
    for i, diff in changes:
        conn.execute(f"UPDATE articles SET {', '.join(f'{k} = ?' for k in diff)} WHERE id = ?", (*diff.values(), i))
    for new_title, aid, old in heads:
        conn.execute("UPDATE OR IGNORE headlines SET title = ? WHERE article_id = ? AND title = ?", (new_title, aid, old))
        conn.execute("DELETE FROM headlines WHERE article_id = ? AND title = ?", (aid, old))   # duplicate after cleaning
    conn.commit()


if __name__ == "__main__":
    main()
