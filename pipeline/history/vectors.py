"""History vectors outside the database (A61, A65): ≈ 6 M articles × 1,024 dimensions is ≈ 26 GB as SQLite float32
blobs, so the vectors live in .npy files (float16 halves it again) and the database keeps only article → part, row.

A part is the articles of a span of days (--start/--end, by publication date), embedded on its own, so a span can be
judged as soon as it and its 7-day margin are embedded; the parts live side by side under one directory:

    PYTHONPATH=. uv run python -m pipeline.history.vectors export --model BAAI/bge-m3 --out data/history/vec/2019 \\
        --start 2018-12-25 --end 2019-12-31
    #   (PC, GPU) python infra/pc/embed_texts.py --model BAAI/bge-m3 --input texts.jsonl --output vectors.npy --dtype float16
    PYTHONPATH=. uv run python -m pipeline.history.vectors import --model bge-m3 --dir data/history/vec/2019
    ... pipeline.history.replay --prefetched --embed-model bge-m3 --vectors data/history/vec ...

What is embedded is what the replay has always embedded for a history article: "headline\\ndescription", with the
model's instruction in front when it needs one (E8). The replay reads only its 7-day window from the files.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import numpy as np

from pipeline import store

ROWS = """CREATE TABLE IF NOT EXISTS vector_rows (
    model TEXT NOT NULL, article_id TEXT NOT NULL, part TEXT NOT NULL, row INTEGER NOT NULL,
    PRIMARY KEY (model, article_id))"""


def text_of(r) -> str:
    return f"{r['title']}\n{r['summary'] or ''}".strip()


def export(conn, out: Path, prefix: str = "", start: dt.date | None = None, end: dt.date | None = None) -> int:
    """The stored history articles published from `start` to `end` (days, both included; open when None), in
    publication order: texts.jsonl (one text a line) and ids.txt (same order)."""
    out.mkdir(parents=True, exist_ok=True)
    lo = f"{start.isoformat()}T00:00:00Z" if start else ""
    hi = f"{(end + dt.timedelta(days=1)).isoformat()}T00:00:00Z" if end else "~"
    n = 0
    with open(out / "texts.jsonl", "w", encoding="utf-8") as ft, open(out / "ids.txt", "w") as fi:
        for r in conn.execute("SELECT id, title, summary FROM articles WHERE published >= ? AND published < ? "
                              "ORDER BY published, id", (lo, hi)):
            ft.write(json.dumps({"t": prefix + text_of(r)}, ensure_ascii=False) + "\n")
            fi.write(r["id"] + "\n")
            n += 1
    (out / "export.json").write_text(json.dumps({"articles": n, "prefix": prefix, "start": start and start.isoformat(),
                                                 "end": end and end.isoformat()}))
    return n


def import_index(conn, model: str, src: Path) -> int:
    """Record article → (part, row) for the vectors in src/vectors.npy (written by infra/pc/embed_texts.py from
    texts.jsonl). The part is src's name; importing a part again replaces it, and an article in two parts keeps the
    last one imported."""
    ids = (src / "ids.txt").read_text().split()
    V = np.load(src / "vectors.npy", mmap_mode="r")
    if len(ids) != V.shape[0]:
        raise SystemExit(f"{len(ids):,} ids but {V.shape[0]:,} vectors: embed texts.jsonl again")
    done = Path(str(src / "vectors.npy") + ".done")
    if done.exists() and int(done.read_text()) != len(ids):
        raise SystemExit(f"the embedding stopped at {done.read_text()} of {len(ids):,}: run embed_texts.py again")
    conn.execute(ROWS)
    conn.execute("DELETE FROM vector_rows WHERE model = ? AND part = ?", (model, src.name))
    for k in range(0, len(ids), 50_000):
        conn.executemany("INSERT OR REPLACE INTO vector_rows VALUES (?,?,?,?)",
                         [(model, a, src.name, k + i) for i, a in enumerate(ids[k:k + 50_000])])
    conn.commit()
    return len(ids)


class VectorFile:
    """The vectors of one model: rows of <root>/<part>/vectors.npy, found through vector_rows."""

    def __init__(self, root: Path | str, model: str):
        self.root, self.model = Path(root), model
        self.parts: dict[str, np.ndarray] = {}

    def part(self, name: str) -> np.ndarray:
        if name not in self.parts:
            self.parts[name] = np.load(self.root / name / "vectors.npy", mmap_mode="r")
        return self.parts[name]

    def rows_for(self, conn, ids: list[str]) -> tuple[list[str], np.ndarray]:
        """(the ids that have a vector, their vectors as float32), in the order asked."""
        got: dict[str, tuple[str, int]] = {}
        for k in range(0, len(ids), 900):
            chunk = ids[k:k + 900]
            got.update((a, (p, r)) for a, p, r in conn.execute(
                f"SELECT article_id, part, row FROM vector_rows WHERE model = ? AND article_id IN "
                f"({','.join('?' * len(chunk))})", (self.model, *chunk)))
        found = [i for i in ids if i in got]
        M = None
        for name in sorted({got[i][0] for i in found}):
            at = [k for k, i in enumerate(found) if got[i][0] == name]
            rows = np.array([got[found[k]][1] for k in at], dtype=int)
            order = np.argsort(rows)                             # read the file forwards, then restore the order
            V = self.part(name)
            if M is None:
                M = np.empty((len(found), V.shape[1]), dtype=np.float32)
            M[np.array(at)[order]] = np.asarray(V[rows[order]], dtype=np.float32)
        if M is None:                                            # nothing found: still the model's width
            any_part = conn.execute("SELECT part FROM vector_rows WHERE model = ? LIMIT 1", (self.model,)).fetchone()
            M = np.empty((0, self.part(any_part[0]).shape[1] if any_part else 0), dtype=np.float32)
        return found, M


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["export", "import"])
    ap.add_argument("--db", default="data/history/history.sqlite")
    ap.add_argument("--model", required=True, help="export: the Hugging Face id; import: the name the replay will use")
    ap.add_argument("--out", type=Path, help="export: the part's directory (a subdirectory of the replay's --vectors)")
    ap.add_argument("--start", type=dt.date.fromisoformat, help="export: the first publication day (default: the first)")
    ap.add_argument("--end", type=dt.date.fromisoformat, help="export: the last publication day, included (default: the last)")
    ap.add_argument("--dir", type=Path, help="import: the part's directory")
    args = ap.parse_args()
    conn = store.connect(args.db)
    if args.what == "export":
        from pipeline.experiments.e8 import INSTRUCT
        needs = args.model in ("Qwen/Qwen3-Embedding-0.6B", "intfloat/multilingual-e5-large-instruct")   # as in E8
        n = export(conn, args.out, INSTRUCT if needs else "", args.start, args.end)
        print(f"{n:,} articles → {args.out}/texts.jsonl")
    else:
        print(f"{import_index(conn, args.model, args.dir):,} vectors indexed for {args.model}")


if __name__ == "__main__":
    main()
