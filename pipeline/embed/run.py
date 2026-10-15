"""M4 run: embed articles that have no vector yet for the configured model.

    uv run --group ml python -m pipeline.embed.run [--days 14]
One vector per canonical article (M3). With a headline history (A34) each version is embedded as
headline + body and the article vector is the L2-normalised mean (A35): MiniLM reads ~128 tokens, so concatenating
versions would push the body out. Body = summary or first 1000 chars of full text. An input hash per vector means
a new headline version triggers a re-embed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib

import numpy as np

from pipeline import store
from pipeline.embed.backend import get_encoder
from pipeline.settings import load_settings

MODEL = load_settings().embed_model   # A12, A45: MiniLM baseline or openai:<model>; vectors are stored per model


def body_text(r) -> str:
    return r["summary"] or (r["full_text"] or "")[:1000]


def version_texts(titles: list[str], body: str) -> list[str]:
    """What M4 embeds for one article: one text per headline version (A35)."""
    return [f"{t}\n{body}".strip() for t in titles]


def input_hash(titles: list[str], body: str) -> str:
    return hashlib.sha1(("\x1f".join(titles) + "\x1e" + body).encode()).hexdigest()


def mean_vectors(V: np.ndarray, counts: list[int]) -> np.ndarray:
    """Average consecutive blocks of rows (one block per article) and L2-normalise."""
    out, k = [], 0
    for n in counts:
        v = V[k:k + n].mean(axis=0)
        out.append(v / (np.linalg.norm(v) or 1.0))
        k += n
    return np.array(out, dtype=np.float32)


def embed_window(conn, model_name: str, days: int, batch: int = 64) -> tuple[int, int, int]:
    """Embed canonical articles of the last `days` whose input changed. Returns (articles, with history, versions)."""
    since = (dt.datetime.now(dt.UTC) - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = conn.execute("""SELECT id, title, summary, full_text FROM articles
                           WHERE canonical_id IS NULL AND kind IS NULL AND published >= ?""", (since,)).fetchall()
    hist = store.headline_history(conn, [r["id"] for r in rows])
    have = {r["article_id"]: r["input_hash"] for r in
            conn.execute("SELECT article_id, input_hash FROM embeddings WHERE model = ?", (model_name,))}
    todo = []
    for r in rows:
        titles = [h.title for h in hist.get(r["id"], [])] or [r["title"] or ""]
        body = body_text(r)
        h = input_hash(titles, body)
        if have.get(r["id"]) != h:
            todo.append((r["id"], version_texts(titles, body), h))
    if not todo:
        return 0, 0, 0
    enc = get_encoder(model_name, conn=conn)
    flat = [t for _, texts, _ in todo for t in texts]
    V = enc.encode(flat, batch_size=batch, normalize_embeddings=True)
    ok = np.isfinite(V).all(axis=1)                          # rows the API rejected are NaN (backend: one bad text
    keep, rows, k = [], [], 0                                # never fails the step): average only the good versions
    for aid, texts, h in todo:
        good = [i for i in range(k, k + len(texts)) if ok[i]]
        if good:
            keep.append((aid, h)); rows.append(mean_vectors(V[good], [len(good)])[0])
        k += len(texts)
    if len(keep) < len(todo):
        print(f"skipped {len(todo) - len(keep)} article(s) the embedding API rejected (logged as embedding_rejected)")
    if keep:
        store.save_embeddings(conn, model_name, [a for a, _ in keep], np.array(rows), [h for _, h in keep])
    conn.commit()
    return len(keep), sum(len(texts) > 1 for _, texts, _ in todo), len(flat)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14, help="only embed articles published in the last N days")
    ap.add_argument("--batch", type=int, default=64)
    args = ap.parse_args()
    n, n_multi, n_versions = embed_window(store.connect(), MODEL, args.days, args.batch)
    print(f"embedded {n} articles ({n_multi} with a headline history, {n_versions} headline versions) with {MODEL}"
          if n else "nothing to embed")


if __name__ == "__main__":
    main()
