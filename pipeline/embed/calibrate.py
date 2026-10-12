"""Calibrate the similarity thresholds for a new embedding model against the MiniLM baseline (A47).

    uv run --group ml python -m pipeline.embed.calibrate --model openai:text-embedding-3-small [--days 7]

1. Story threshold (2.3): embed the window with the new model, cluster at a sweep of thresholds and pick the one that
   keeps the baseline's precision on the labelled pairs (data/eval/*.jsonl) with the best recall — same operating point.
2. 2.1 thresholds: quantile mapping on the same texts. If 0.15 is the 1st percentile of headline↔opening similarity
   under MiniLM, the new threshold is the 1st percentile under the new model. Done per kind of comparison:
   headline↔opening (wall_max, owns_text), headline↔headline (same_story, same_title), summary↔summary (same_summary).
3. Writes the profile (data/embedding_profiles.json) and prints MiniLM vs new side by side. Switch with
   BASEDNEWS_EMBED_MODEL=<model> in .env once the numbers look right.
Cost with an OpenAI model: ~1–2 M tokens (≈ $0.02–0.04 at text-embedding-3-small prices).
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import numpy as np

from pipeline import store
from pipeline.embed.backend import get_encoder
from pipeline.embed.profiles import MINILM_PROFILE, save_profile
from pipeline.embed.run import embed_window
from pipeline.events.cluster import ClusterConfig, cluster
from pipeline.events.eval import prf
from pipeline.events.run import load_window
from pipeline.settings import MINILM

N_SAMPLE = 2000
LABELS = (Path("data/eval/same_event_pairs.jsonl"), Path("data/eval/deployed_pairs_claude.jsonl"))


def sweep(conn, model, days, gold, thresholds):
    rows, V, hours = load_window(conn, days, model)
    ids = [r["id"] for r in rows]
    out = {}
    for t in thresholds:
        p, r, f, _ = prf(dict(zip(ids, cluster(V, hours, ClusterConfig(method="average", threshold=t)))), gold)
        out[round(float(t), 3)] = (p, r, f)
    return out


def sims(enc, a: list[str], b: list[str]) -> np.ndarray:
    A, B = enc.encode(a, normalize_embeddings=True), enc.encode(b, normalize_embeddings=True)
    return (A * B).sum(1)


def qmap(t_old: float, base: np.ndarray, new: np.ndarray) -> float:
    """Interpolated quantile mapping (exact identity when base == new): t_old's rank under the baseline → same rank."""
    xb, xn = np.sort(base), np.sort(new)
    pb, pn = (np.arange(len(xb)) + 0.5) / len(xb), (np.arange(len(xn)) + 0.5) / len(xn)
    return round(float(np.interp(np.interp(t_old, xb, pb), pn, xn)), 4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()
    conn = store.connect()
    gold = [(d["a"], d["b"], d["same"]) for f in LABELS if f.exists() for d in map(json.loads, f.read_text().splitlines())]
    print(f"labelled pairs: {len(gold)}")

    # 1. story threshold at the baseline's precision
    for m in (MINILM, args.model):
        n, _, _ = embed_window(conn, m, args.days + 7)
        print(f"embedded {n} new articles with {m}")
    base = sweep(conn, MINILM, args.days, gold, [MINILM_PROFILE["story"]])[MINILM_PROFILE["story"]]
    grid = sweep(conn, args.model, args.days, gold, np.round(np.arange(0.30, 0.91, 0.02), 2))
    ok = {t: v for t, v in grid.items() if v[0] >= base[0] - 1e-9}
    story = max(ok, key=lambda t: (ok[t][1], -t)) if ok else max(grid, key=lambda t: grid[t][2])
    print(f"\n2.3 story threshold: MiniLM {MINILM_PROFILE['story']} → P {base[0]:.2f} R {base[1]:.2f} F1 {base[2]:.2f}"
          f" | {args.model} {story} → P {grid[story][0]:.2f} R {grid[story][1]:.2f} F1 {grid[story][2]:.2f}")

    # 2. quantile-map the 2.1 thresholds on identical texts
    rng = random.Random(0)
    rows = conn.execute("""SELECT id, outlet_id, title, summary, full_text FROM articles
                           WHERE canonical_id IS NULL AND kind IS NULL AND full_text IS NOT NULL""").fetchall()
    rows = rng.sample(rows, min(N_SAMPLE, len(rows)))
    heads, leads = [r["title"] or "" for r in rows], [r["full_text"][:400] for r in rows]
    # headline and summary pairs: half from inside the same story (the high-similarity range the thresholds sit in),
    # half at random (the bulk) — random pairs alone almost never reach 0.6–0.75
    run = store.latest_event_run(conn)
    art = {r["id"]: r for r in conn.execute("SELECT id, title, summary FROM articles")}
    members = {}
    for r in conn.execute("SELECT event_id, article_id FROM event_members WHERE run_id = ?", (run,)):
        members.setdefault(r["event_id"], []).append(r["article_id"])
    within = [(a, b) for m in members.values() if len(m) > 1 for a, b in zip(m[::2], m[1::2])]
    within = rng.sample(within, min(N_SAMPLE // 2, len(within)))
    rand = [(a["id"], b["id"]) for a, b in zip(rows[::2], rows[1::2])][:N_SAMPLE // 2]
    pairs = [(art[a], art[b]) for a, b in within + rand if a in art and b in art]
    tpairs = [(a["title"] or "", b["title"] or "") for a, b in pairs]
    spairs = [(a["summary"], b["summary"]) for a, b in pairs if a["summary"] and b["summary"]]
    out = {"story": story}
    for m_name, store_as in ((MINILM, "base"), (args.model, "new")):
        enc = get_encoder(m_name, conn=conn)
        globals()[f"hl_{store_as}"] = sims(enc, heads, leads)
        globals()[f"hh_{store_as}"] = sims(enc, [a for a, _ in tpairs], [b for _, b in tpairs])
        globals()[f"ss_{store_as}"] = sims(enc, [a for a, _ in spairs], [b for _, b in spairs])
    g = globals()
    out["wall_max"] = qmap(MINILM_PROFILE["wall_max"], g["hl_base"], g["hl_new"])
    out["owns_text"] = qmap(MINILM_PROFILE["owns_text"], g["hl_base"], g["hl_new"])
    out["same_story"] = qmap(MINILM_PROFILE["same_story"], g["hh_base"], g["hh_new"])
    out["same_title"] = qmap(MINILM_PROFILE["same_title"], g["hh_base"], g["hh_new"])
    out["same_summary"] = qmap(MINILM_PROFILE["same_summary"], g["ss_base"], g["ss_new"])
    out["owns_margin"] = MINILM_PROFILE["owns_margin"]
    print(f"\n{'threshold':<14}{'MiniLM':>8}{args.model:>36}")
    for k in ("story", "wall_max", "owns_text", "same_story", "same_title", "same_summary", "owns_margin"):
        print(f"{k:<14}{MINILM_PROFILE[k]:>8}{out[k]:>36}")
    out.update(source=f"calibrated against MiniLM on {len(rows)} articles, {len(tpairs)} headline pairs, "
                      f"{len(spairs)} summary pairs, {len(gold)} labelled pairs",
               story_metrics={"precision": grid[story][0], "recall": grid[story][1], "f1": grid[story][2]})
    save_profile(args.model, out)
    print(f"\nsaved profile for {args.model} → data/embedding_profiles.json. Switch: BASEDNEWS_EMBED_MODEL={args.model}")


if __name__ == "__main__":
    main()
