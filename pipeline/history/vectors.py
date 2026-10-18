"""History vectors outside the database (A61, A65): ≈ 6 M articles × 1,024 dimensions is ≈ 26 GB as SQLite float32
blobs, so the vectors live in one .npy file (float16 halves it again) and the database keeps only article → row.

    PYTHONPATH=. uv run python -m pipeline.history.vectors export --model BAAI/bge-m3 --out data/history/vec-bge-m3
    #   (PC, GPU) python infra/pc/embed_texts.py --model BAAI/bge-m3 --input texts.jsonl --output vectors.npy --dtype float16
    PYTHONPATH=. uv run python -m pipeline.history.vectors import --model bge-m3 --dir data/history/vec-bge-m3
    ... pipeline.history.replay --prefetched --embed-model bge-m3 --vectors data/history/vec-bge-m3 ...

What is embedded is what the replay has always embedded for a history article: "headline\\ndescription", with the
model's instruction in front when it needs one (E8). The replay reads only its 7-day window from the file.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from pipeline import store

ROWS = """CREATE TABLE IF NOT EXISTS vector_rows (
    model TEXT NOT NULL, article_id TEXT NOT NULL, row INTEGER NOT NULL, PRIMARY KEY (model, article_id))"""


def text_of(r) -> str:
    return f"{r['title']}\n{r['summary'] or ''}".strip()


def export(conn, out: Path, prefix: str = "") -> int:
    """Every stored history article, in publication order: texts.jsonl (one text a line) and ids.txt (same order)."""
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(out / "texts.jsonl", "w", encoding="utf-8") as ft, open(out / "ids.txt", "w") as fi:
        for r in conn.execute("SELECT id, title, summary FROM articles ORDER BY published, id"):
            ft.write(json.dumps({"t": prefix + text_of(r)}, ensure_ascii=False) + "\n")
            fi.write(r["id"] + "\n")
            n += 1
    (out / "export.json").write_text(json.dumps({"articles": n, "prefix": prefix}))
    return n


def import_index(conn, model: str, src: Path) -> int:
    """Record article → row for the vectors in src/vectors.npy (written by infra/pc/embed_texts.py from texts.jsonl)."""
    ids = (src / "ids.txt").read_text().split()
    V = np.load(src / "vectors.npy", mmap_mode="r")
    if len(ids) != V.shape[0]:
        raise SystemExit(f"{len(ids):,} ids but {V.shape[0]:,} vectors: embed texts.jsonl again")
    done = Path(str(src / "vectors.npy") + ".done")
    if done.exists() and int(done.read_text()) != len(ids):
        raise SystemExit(f"the embedding stopped at {done.read_text()} of {len(ids):,}: run embed_texts.py again")
    conn.execute(ROWS)
    conn.execute("DELETE FROM vector_rows WHERE model = ?", (model,))
    for k in range(0, len(ids), 50_000):
        conn.executemany("INSERT INTO vector_rows VALUES (?,?,?)", [(model, a, k + i) for i, a in enumerate(ids[k:k + 50_000])])
    conn.commit()
    return len(ids)


class VectorFile:
    """The vectors of one model: rows of src/vectors.npy, found through vector_rows."""

    def __init__(self, src: Path | str, model: str):
        self.src, self.model = Path(src), model
        self.V = np.load(self.src / "vectors.npy", mmap_mode="r")

    def rows_for(self, conn, ids: list[str]) -> tuple[list[str], np.ndarray]:
        """(the ids that have a vector, their vectors as float32), in the order asked."""
        got: dict[str, int] = {}
        for k in range(0, len(ids), 900):
            chunk = ids[k:k + 900]
            got.update(conn.execute(f"SELECT article_id, row FROM vector_rows WHERE model = ? AND article_id IN "
                                    f"({','.join('?' * len(chunk))})", (self.model, *chunk)).fetchall())
        found = [i for i in ids if i in got]
        rows = np.array([got[i] for i in found], dtype=int)
        order = np.argsort(rows)                                 # read the file forwards, then restore the order
        M = np.empty((len(found), self.V.shape[1]), dtype=np.float32)
        if found:
            M[order] = np.asarray(self.V[rows[order]], dtype=np.float32)
        return found, M


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["export", "import"])
    ap.add_argument("--db", default="data/history/history.sqlite")
    ap.add_argument("--model", required=True, help="export: the Hugging Face id; import: the name the replay will use")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--dir", type=Path)
    args = ap.parse_args()
    conn = store.connect(args.db)
    if args.what == "export":
        from pipeline.experiments.e8 import INSTRUCT
        needs = args.model in ("Qwen/Qwen3-Embedding-0.6B", "intfloat/multilingual-e5-large-instruct")   # as in E8
        n = export(conn, args.out, INSTRUCT if needs else "")
        print(f"{n:,} articles → {args.out}/texts.jsonl")
    else:
        print(f"{import_index(conn, args.model, args.dir):,} vectors indexed for {args.model}")


if __name__ == "__main__":
    main()
