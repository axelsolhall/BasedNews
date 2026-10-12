"""Build the M5 ground truth: a person labels article pairs as same event or not.

    uv run --group ml python -m pipeline.events.label [--n 150]
Pairs are sampled across similarity bands (so the set has easy and hard cases) and are ~60 % cross-country (A07).
Answers: y = same event · n = different · s = skip/unsure · q = quit (progress is saved after every answer).
Labels: data/eval/same_event_pairs.jsonl
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from pipeline import store
from pipeline.events.cluster import ClusterConfig, time_aware_similarity
from pipeline.events.run import load_window

OUT = Path("data/eval/same_event_pairs.jsonl")
BANDS = [(0.30, 0.45), (0.45, 0.55), (0.55, 0.65), (0.65, 0.75), (0.75, 1.01)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    conn = store.connect()
    rows, V, hours = load_window(conn, args.days)
    S = time_aware_similarity(V, hours, ClusterConfig())
    meta = {r["id"]: dict(conn.execute("SELECT country, outlet_id, title, summary FROM articles WHERE id=?",
                                       (r["id"],)).fetchone()) for r in rows}
    done = set()
    if OUT.exists():
        for line in OUT.read_text().splitlines():
            d = json.loads(line); done.add((d["a"], d["b"]))
    rng = np.random.default_rng(args.seed)
    iu, ju = np.triu_indices(len(rows), 1)
    sims = S[iu, ju]
    per_band = args.n // len(BANDS)
    pairs = []
    for lo, hi in BANDS:
        idx = np.where((sims >= lo) & (sims < hi))[0]
        cross = [k for k in idx if rows[iu[k]]["country"] != rows[ju[k]]["country"]]
        same = [k for k in idx if rows[iu[k]]["country"] == rows[ju[k]]["country"]]
        take = list(rng.choice(cross, size=min(len(cross), int(per_band * 0.6)), replace=False)) if cross else []
        take += list(rng.choice(same, size=min(len(same), per_band - len(take)), replace=False)) if same else []
        pairs += [(rows[iu[k]]["id"], rows[ju[k]]["id"], float(sims[k])) for k in take]
    rng.shuffle(pairs)
    pairs = [p for p in pairs if (p[0], p[1]) not in done]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    print(f"{len(pairs)} pairs to label (already labelled: {len(done)}). y/n/s/q\n")
    with OUT.open("a") as f:
        for k, (a, b, s) in enumerate(pairs, 1):
            A, B = meta[a], meta[b]
            print(f"── {k}/{len(pairs)}")
            print(f"A [{A['country']}/{A['outlet_id']}] {A['title']}\n   {(A['summary'] or '')[:220]}")
            print(f"B [{B['country']}/{B['outlet_id']}] {B['title']}\n   {(B['summary'] or '')[:220]}")
            ans = input("same event? [y/n/s/q] ").strip().lower()
            if ans == "q":
                break
            if ans in ("y", "n"):
                f.write(json.dumps({"a": a, "b": b, "same": ans == "y", "sim": round(s, 4)}) + "\n"); f.flush()
    print(f"saved to {OUT}")


if __name__ == "__main__":
    main()
