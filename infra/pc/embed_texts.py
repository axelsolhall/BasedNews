"""Embed a file of texts on the gaming PC's GPU (A65): one JSON line per text in, one float32 row per text out.

    systemctl --user stop llm-judge          # the judge holds most of the GPU's memory; start it again afterwards
    ~/llm/.venv/bin/python embed_texts.py --model BAAI/bge-m3 --input texts.jsonl --output vectors.npy

Each input line is {"t": "<text>"}, exactly what is to be embedded: any prefix (an instruction) is already applied
by whoever wrote the file (pipeline.experiments.e8 export, the history replay). The output is an .npy file written as
it goes (a memory map), so millions of texts need no more memory than one chunk, and a run that stops resumes from
the last finished chunk (`<output>.done` holds how many rows are final). Rows are L2-normalised; inputs are capped
at 512 tokens (a headline and a snippet are ≈ 90 on average). No BasedNews imports: it runs from a copy of
infra/pc with only sentence-transformers installed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

CHUNK = 50_000                         # texts per chunk: one chunk of text and vectors in memory at a time
MAX_TOKENS = 512


def count_lines(path: Path) -> int:
    with open(path, "rb") as f:
        return sum(1 for _ in f)


def embed_file(model, src: Path, dst: Path, batch: int = 256, chunk: int = CHUNK, dtype: str = "float32") -> int:
    """Embed every line of `src` into rows of `dst`, resuming after the rows `dst.done` says are final. dtype
    float16 halves the file (≈ 13 GB instead of 26 for 6 M × 1,024); vectors are normalised before rounding."""
    src, dst = Path(src), Path(dst)
    done = Path(str(dst) + ".done")
    n, dim = count_lines(src), model.get_sentence_embedding_dimension()
    start = int(done.read_text()) if done.exists() and dst.exists() else 0
    out = np.lib.format.open_memmap(dst, mode="r+" if start else "w+", dtype=np.dtype(dtype), shape=(n, dim))
    if out.shape != (n, dim):
        raise SystemExit(f"{dst} has shape {out.shape}, the input needs {(n, dim)}: delete it and {done} to start over")
    pos, buf = start, []

    def flush():
        nonlocal pos, buf
        if buf:
            out[pos:pos + len(buf)] = model.encode(buf, batch_size=batch, normalize_embeddings=True,
                                                   convert_to_numpy=True).astype(out.dtype)
            pos += len(buf)
            out.flush()
            done.write_text(str(pos))
            print(f"{pos:,}/{n:,}", flush=True)
            buf = []

    with open(src, encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i < start:
                continue
            buf.append(json.loads(line)["t"])
            if len(buf) == chunk:
                flush()
    flush()
    return pos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--batch", type=int, default=256)
    ap.add_argument("--dtype", choices=["float32", "float16"], default="float32")
    args = ap.parse_args()
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(args.model, device="cuda")
    model.max_seq_length = min(MAX_TOKENS, model.max_seq_length or MAX_TOKENS)
    print(f"{embed_file(model, args.input, args.output, args.batch, dtype=args.dtype):,} texts → {args.output}")


if __name__ == "__main__":
    main()
