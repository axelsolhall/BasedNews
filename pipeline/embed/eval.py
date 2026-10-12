"""M4 eval: do nearest neighbours make sense, including across languages?

    uv run --group ml python -m pipeline.embed.eval [--n 5]
Prints, for N random recent articles, their 3 nearest neighbours from OTHER countries with cosine similarity.
Good: neighbours are clearly the same story in another language. Also prints the similarity distribution.
"""
from __future__ import annotations

import argparse

import numpy as np

from pipeline import store
from pipeline.embed.run import MODEL


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    conn = store.connect()
    ids, V = store.load_embeddings(conn, MODEL)
    if len(ids) < 10:
        print("not enough embeddings — run pipeline.embed.run first"); return
    meta = {r["id"]: r for r in conn.execute("SELECT id, country, outlet_id, title FROM articles WHERE id IN (%s)"
                                             % ",".join("?" * len(ids)), ids)}
    rng = np.random.default_rng(args.seed)
    S = V @ V.T
    off = S[np.triu_indices(len(ids), 1)]
    print(f"{len(ids)} vectors · pairwise cosine: median {np.median(off):.2f}, p99 {np.percentile(off, 99):.2f}, "
          f"p99.9 {np.percentile(off, 99.9):.2f}\n")
    for i in rng.choice(len(ids), size=min(args.n, len(ids)), replace=False):
        a = meta[ids[i]]
        print(f"■ [{a['country']}/{a['outlet_id']}] {a['title']}")
        order = np.argsort(-S[i])
        shown = 0
        for j in order:
            b = meta[ids[j]]
            if b["country"] == a["country"]:
                continue
            print(f"    {S[i, j]:.2f}  [{b['country']}/{b['outlet_id']}] {b['title']}")
            shown += 1
            if shown == 3:
                break
        print()


if __name__ == "__main__":
    main()
