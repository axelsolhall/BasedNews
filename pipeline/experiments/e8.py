"""E8: local embeddings for the history vs live's OpenAI @512 (docs/experiments/e8_prereg.json). Free, local.

    uv run --group ml python -m pipeline.experiments.e8 embed --model bge-m3     # one local model, on this machine
    uv run python -m pipeline.experiments.e8 export                              # or on the PC's GPU: texts out …
    #   (PC) ~/llm/.venv/bin/python infra/pc/embed_texts.py --model BAAI/bge-m3 --input texts_bge-m3.jsonl --output bge-m3.texts.npy
    uv run python -m pipeline.experiments.e8 import --model bge-m3 --vectors bge-m3.texts.npy    # … vectors back in
    uv run --group ml python -m pipeline.experiments.e8 sample                   # anchors + pooled candidates
    uv run python -m pipeline.experiments.e8 show [--from N] [--n 40]            # blind labelling view
    uv run python -m pipeline.experiments.e8 analyse                             # → reports/e8.json, reports/e8.md

The live 7-day window of the production snapshot (2026-10-16, 41 countries). Every model embeds what live embeds
(headline versions + body, mean, L2-normalised). Pooled evaluation with equal depth, as E7b: random anchors, each
model's top 4 other-outlet articles in the 72 h window, the union labelled blind. Only the anchors' similarity rows
are computed, and the window is clustered with average_sparse (the full matrix would be 14 GB).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path

import numpy as np

from pipeline import store
from pipeline.embed.run import body_text, version_texts
from pipeline.events.cluster import ClusterConfig, _distances, average_sparse

PREREG = Path("docs/experiments/e8_prereg.json")
DB = Path("data/prod_snapshot.sqlite")
WINDOW = ("2026-10-09T16:00:00Z", "2026-10-16T16:00:00Z")
VECS = Path("data/e8_vectors")
POOL, LABELS = Path("data/eval/e8_pool.jsonl"), Path("data/eval/e8_labels.jsonl")
REFERENCE = "OpenAI 512"
LOCAL = {"Qwen3-Embedding-0.6B": "Qwen/Qwen3-Embedding-0.6B", "bge-m3": "BAAI/bge-m3",
         "e5-large-instruct": "intfloat/multilingual-e5-large-instruct"}
INSTRUCT = "Instruct: Retrieve news articles that report the same event\nQuery: "
NEEDS_INSTRUCTION = {"Qwen3-Embedding-0.6B", "e5-large-instruct"}
N_ANCHORS, DEPTH, SEED = 40, 4, 2027
GRID = [round(float(t), 2) for t in np.arange(0.30, 0.91, 0.02)]
LIVE_THRESHOLD, BOOT = 0.56, 2000


def window(conn) -> list:
    """The window's canonical articles that have live's OpenAI vector, in a fixed order."""
    rows = conn.execute("""SELECT a.id, a.outlet_id, a.published, a.title, a.summary, a.full_text FROM articles a
                           JOIN embeddings e ON e.article_id = a.id AND e.model = 'openai:text-embedding-3-small@512'
                           WHERE a.published >= ? AND a.published < ? AND a.published LIKE '____-__-__T%Z'
                           AND a.canonical_id IS NULL AND a.kind IS NULL ORDER BY a.id""", WINDOW).fetchall()
    return rows


def hours(rows) -> np.ndarray:
    return np.array([dt.datetime.fromisoformat(r["published"].replace("Z", "+00:00")).timestamp() / 3600 for r in rows])


def texts_of(conn, rows) -> tuple[list[str], list[int]]:
    """What live embeds per article (headline versions + body), flattened, and how many texts each article has."""
    hist = store.headline_history(conn, [r["id"] for r in rows])
    texts, counts = [], []
    for r in rows:
        t = version_texts([h.title for h in hist.get(r["id"], [])] or [r["title"] or ""], body_text(r))
        texts += t
        counts.append(len(t))
    return texts, counts


def prefix(name: str) -> str:
    return INSTRUCT if name in NEEDS_INSTRUCTION else ""


def mean_per_article(E: np.ndarray, counts: list[int]) -> np.ndarray:
    """One vector per article: the L2-normalised mean of its versions' vectors (A35)."""
    out, k = [], 0
    for n in counts:
        v = np.asarray(E[k:k + n], dtype=np.float32).mean(axis=0)
        out.append(v / (np.linalg.norm(v) or 1.0))
        k += n
    if k != len(E):
        raise SystemExit(f"{len(E)} text vectors for {k} texts: export and embed again")
    return np.array(out, dtype=np.float32)


def save(name: str, rows, A: np.ndarray) -> None:
    VECS.mkdir(parents=True, exist_ok=True)
    np.save(VECS / f"{name}.npy", A)
    (VECS / f"{name}.ids").write_text("\n".join(r["id"] for r in rows))
    print(f"{name}: {len(rows)} articles → {VECS / name}.npy")


def device() -> str:
    import torch
    return "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"


def embed(name: str) -> None:
    from pipeline import net  # noqa: F401  (BASEDNEWS_IPV4=1: the model download avoids a broken IPv6 route)
    from sentence_transformers import SentenceTransformer
    conn = store.connect(DB)
    rows = window(conn)
    texts, counts = texts_of(conn, rows)
    model = SentenceTransformer(LOCAL[name], device=device())
    model.max_seq_length = 512
    E = model.encode([prefix(name) + t for t in texts], batch_size=64, normalize_embeddings=True,
                     show_progress_bar=True, convert_to_numpy=True).astype(np.float32)
    save(name, rows, mean_per_article(E, counts))


def export() -> None:
    """The texts each local model must embed, one JSON line per text (instruction applied), for infra/pc/embed_texts.py."""
    conn = store.connect(DB)
    rows = window(conn)
    texts, counts = texts_of(conn, rows)
    VECS.mkdir(parents=True, exist_ok=True)
    for name in LOCAL:
        with open(VECS / f"texts_{name}.jsonl", "w", encoding="utf-8") as f:
            for t in texts:
                f.write(json.dumps({"t": prefix(name) + t}, ensure_ascii=False) + "\n")
    (VECS / "counts.json").write_text(json.dumps({"ids": [r["id"] for r in rows], "counts": counts}))
    print(f"{len(rows)} articles, {len(texts)} texts → {VECS}/texts_<model>.jsonl (+ counts.json)")


def import_vectors(name: str, path: Path) -> None:
    """Per-text vectors from the PC → one vector per article, checked against the exported order."""
    conn = store.connect(DB)
    rows = window(conn)
    meta = json.loads((VECS / "counts.json").read_text())
    if meta["ids"] != [r["id"] for r in rows]:
        raise SystemExit("the window changed since export: export and embed again")
    save(name, rows, mean_per_article(np.load(path, mmap_mode="r"), meta["counts"]))


def vectors(conn, rows) -> dict[str, np.ndarray]:
    ids = [r["id"] for r in rows]
    got, V = store.load_embeddings(conn, "openai:text-embedding-3-small@512", ids)
    pos = {i: k for k, i in enumerate(got)}              # load_embeddings returns the table's order, not ours
    out = {REFERENCE: V[[pos[i] for i in ids]]}
    for name in LOCAL:
        assert (VECS / f"{name}.ids").read_text().split("\n") == ids, f"{name}: embed it again (different articles)"
        out[name] = np.load(VECS / f"{name}.npy")
    return out


def anchor_rows(V, H, outlets, anchors, cfg) -> np.ndarray:
    """Time-aware similarity of each anchor to every article; same outlet and outside the window → -inf."""
    S = 1.0 - _distances(V, H, cfg, np.array(anchors), slice(None))
    for r, a in enumerate(anchors):
        S[r, outlets == outlets[a]] = -np.inf
    S[S <= -1.0] = -np.inf
    return S


def sample() -> None:
    conn = store.connect(DB)
    rows = window(conn)
    vecs, H, outlets = vectors(conn, rows), hours(rows), np.array([r["outlet_id"] for r in rows])
    rng = np.random.default_rng(SEED)
    anchors = sorted(rng.choice(len(rows), size=N_ANCHORS, replace=False).tolist())   # no model involved
    pool = {}
    for name, V in vecs.items():
        S = anchor_rows(V, H, outlets, anchors, ClusterConfig())
        for r, a in enumerate(anchors):
            for b in np.argsort(-S[r])[:DEPTH]:
                if np.isfinite(S[r, b]):
                    pool.setdefault((rows[a]["id"], rows[b]["id"]), set()).add(name)
    out = [{"anchor": a, "candidate": b} for (a, b) in pool]
    rng.shuffle(out)                                    # blind: the order says nothing about the model
    for k, r in enumerate(out, 1):
        r["pair"] = f"Q{k:03d}"
    POOL.write_text("".join(json.dumps(r) + "\n" for r in out))
    print(f"{N_ANCHORS} anchors, depth {DEPTH}, {len(vecs)} models → {len(out)} pooled pairs → {POOL}")


def text(conn, aid: str) -> str:
    r = conn.execute("SELECT outlet_id, country, published, title, summary, full_text FROM articles WHERE id = ?",
                     (aid,)).fetchone()
    body = (r["summary"] or r["full_text"] or "").replace("\n", " ")[:320]
    return f"[{r['country']} {r['published'][:16]}] {r['title']}\n      {body}"


def show(start: int, n: int) -> None:
    conn = store.connect(DB)
    for r in [json.loads(x) for x in POOL.read_text().splitlines()][start:start + n]:
        print(f"{r['pair']}\n  A {text(conn, r['anchor'])}\n  B {text(conn, r['candidate'])}\n")


def prf(pred, truth):
    tp = int((pred & truth).sum()); fp = int((pred & ~truth).sum()); fn = int((~pred & truth).sum())
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def auc(scores, truth):
    pos, neg = scores[truth], scores[~truth]
    if not len(pos) or not len(neg):
        return float("nan")
    return float(((pos[:, None] > neg[None, :]).sum() + 0.5 * (pos[:, None] == neg[None, :]).sum()) / (len(pos) * len(neg)))


def ci(xs):
    xs = np.asarray(xs, dtype=float)
    xs = xs[np.isfinite(xs)]
    return [round(float(np.percentile(xs, 2.5)), 3), round(float(np.percentile(xs, 97.5)), 3)] if len(xs) else [None, None]


def pair_agreement(labels, ref) -> tuple[float, float]:
    """Pair counting: of `labels`' same-cluster pairs, the share also same-cluster in `ref` (precision), and of `ref`'s,
    the share `labels` keeps (recall)."""
    c2 = lambda x: x * (x - 1) // 2                     # noqa: E731
    _, joint = np.unique(np.stack([labels, ref]), axis=1, return_counts=True)
    both = int(sum(c2(int(n)) for n in joint))
    mine = int(sum(c2(int(n)) for n in np.bincount(labels)))
    theirs = int(sum(c2(int(n)) for n in np.bincount(ref)))
    return (both / mine if mine else 0.0), (both / theirs if theirs else 0.0)


def analyse() -> None:
    conn = store.connect(DB)
    rows = window(conn)
    pos = {r["id"]: k for k, r in enumerate(rows)}
    vecs, H = vectors(conn, rows), hours(rows)
    pool = [json.loads(x) for x in POOL.read_text().splitlines()]
    lab = {d["pair"]: d["same"] for d in map(json.loads, LABELS.read_text().splitlines())}
    missing = [r["pair"] for r in pool if r["pair"] not in lab]
    if missing:
        raise SystemExit(f"{len(missing)} pooled pairs have no label yet: {missing[:5]}…")
    A = np.array([pos[r["anchor"]] for r in pool]); B = np.array([pos[r["candidate"]] for r in pool])
    truth = np.array([bool(lab[r["pair"]]) for r in pool])
    anchor_of = np.array([r["anchor"] for r in pool])
    anchors = sorted(set(anchor_of))
    out = {"prereg_sha256": hashlib.sha256(PREREG.read_bytes()).hexdigest(), "window_articles": len(rows),
           "pairs": len(pool), "same": int(truth.sum()), "anchors": len(anchors), "models": {}}
    cfg = ClusterConfig()
    sims, clus, labels_at = {}, {}, {}
    for name, V in vecs.items():
        sims[name] = np.array([1.0 - _distances(V, H, cfg, np.array([a]), np.array([b]))[0, 0] for a, b in zip(A, B)])
        clus[name] = {}
        for t in GRID + [LIVE_THRESHOLD]:
            L = average_sparse(V, H, ClusterConfig(threshold=t))
            clus[name][t] = L[A] == L[B]
            labels_at[(name, t)] = L
            print(f"  {name} t={t}: {len(np.unique(L))} stories", flush=True)
        out["models"][name] = {"auc": round(auc(sims[name], truth), 3),
                               "sweep": [[t, *[round(x, 3) for x in prf(clus[name][t], truth)]] for t in GRID]}
    ops = {REFERENCE: LIVE_THRESHOLD}
    for name in LOCAL:                                  # E7b's rule: highest F1 among thresholds with precision ≥ 0.90
        ok = [(t, p, r, f) for t, p, r, f in out["models"][name]["sweep"] if p >= 0.90]
        ops[name] = max(ok, key=lambda x: (x[3], -x[0]))[0] if ok else None
    rng = np.random.default_rng(0)                      # bootstrap over anchors
    groups = {a: np.where(anchor_of == a)[0] for a in anchors}
    boots = {n: {"auc": [], "p": [], "r": [], "f": [], "df": []} for n in vecs}
    for _ in range(BOOT):
        idx = np.concatenate([groups[a] for a in rng.choice(anchors, size=len(anchors), replace=True)])
        t_ = truth[idx]
        f_ref = prf(clus[REFERENCE][LIVE_THRESHOLD][idx], t_)[2]
        for n in vecs:
            boots[n]["auc"].append(auc(sims[n][idx], t_))
            if ops[n] is not None:
                p, r, f = prf(clus[n][ops[n]][idx], t_)
                boots[n]["p"].append(p); boots[n]["r"].append(r); boots[n]["f"].append(f); boots[n]["df"].append(f - f_ref)
    for n in vecs:
        m = out["models"][n]
        m["auc_ci"] = ci(boots[n]["auc"])
        if ops[n] is not None:
            p, r, f = prf(clus[n][ops[n]], truth)
            m["clustering"] = {"threshold": ops[n], "precision": round(p, 3), "recall": round(r, 3), "f1": round(f, 3),
                               "precision_ci": ci(boots[n]["p"]), "recall_ci": ci(boots[n]["r"]), "f1_ci": ci(boots[n]["f"])}
            if n != REFERENCE:
                m["M4_f1_minus_live_ci"] = ci(boots[n]["df"])
                m["M3_like_live"] = [round(x, 3) for x in pair_agreement(labels_at[(n, ops[n])],
                                                                         labels_at[(REFERENCE, LIVE_THRESHOLD)])]
    best = max(LOCAL, key=lambda n: out["models"][n]["auc"])
    ci4 = out["models"][best].get("M4_f1_minus_live_ci", [None, None])
    out["decision"] = {"history_model": best, "threshold": ops[best],
                       "valid": ci4[0] is not None and ci4[1] >= 0}
    Path("reports").mkdir(exist_ok=True)
    Path("reports/e8.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k != "models"}, indent=1))
    for n, m in out["models"].items():
        print(n, {k: v for k, v in m.items() if k != "sweep"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["embed", "export", "import", "sample", "show", "analyse"])
    ap.add_argument("--model", choices=list(LOCAL))
    ap.add_argument("--vectors", type=Path, help="import: the per-text .npy written by infra/pc/embed_texts.py")
    ap.add_argument("--from", dest="start", type=int, default=0)
    ap.add_argument("--n", type=int, default=40)
    args = ap.parse_args()
    if args.what == "embed":
        for name in ([args.model] if args.model else LOCAL):
            embed(name)
    elif args.what == "export":
        export()
    elif args.what == "import":
        if not (args.model and args.vectors):
            ap.error("import needs --model and --vectors")
        import_vectors(args.model, args.vectors)
    elif args.what == "sample":
        sample()
    elif args.what == "show":
        show(args.start, args.n)
    else:
        analyse()


if __name__ == "__main__":
    main()
