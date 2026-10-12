"""E7b: unbiased re-test of story matching, MiniLM vs OpenAI @512 (docs/experiments/e7b_prereg.json). Free.

    uv run python -m pipeline.experiments.e7b sample        # anchors + pooled candidates → data/eval/e7b_pool.jsonl
    uv run python -m pipeline.experiments.e7b show [--from N] [--n 40]    # the blind labelling view (no model, no score)
    uv run python -m pipeline.experiments.e7b analyse       # labels in data/eval/e7b_labels.jsonl → reports/e7b_pooled.*
    uv run python -m pipeline.experiments.e7b analyse --addendum   # + OpenAI @1536 at the same depth → reports/e7b_addendum.*

Pooled evaluation with equal depth: random anchors (no model involved), each model's top 4 other-outlet articles per
anchor within the 72 h clustering window, the union labelled blind. Neither model chooses the test set; recall is
relative to the pool. Not a claim (pipeline phase).
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from pipeline import store
from pipeline.events.cluster import ClusterConfig, average_linkage, cut, time_aware_similarity
from pipeline.events.run import load_window
from pipeline.settings import MINILM

PREREG = Path("docs/experiments/e7b_prereg.json")
POOL = Path("data/eval/e7b_pool.jsonl")
LABELS = Path("data/eval/e7b_labels.jsonl")
MODELS = {"MiniLM 384": MINILM, "OpenAI 512": "openai:text-embedding-3-small@512"}
# addendum (asked after the pre-registered run): 1536's top-4 candidates were added to the pool and labelled blind,
# mixed with 30 relabels of existing pairs (agreement in reports/e7b_relabel.json)
MODELS_ADDENDUM = {**MODELS, "OpenAI 1536": "openai:text-embedding-3-small@1536"}
N_ANCHORS, DEPTH, SEED = 40, 4, 2026
GRID = [round(float(t), 2) for t in np.arange(0.30, 0.81, 0.02)]
BOOT = 2000


def window(conn, models=MODELS):
    """All models over the same articles, in the same order."""
    data = {name: load_window(conn, 7, m) for name, m in models.items()}
    ids = sorted(set.intersection(*[{r["id"] for r in rows} for rows, _, _ in data.values()]))
    out = {}
    for name, (rows, V, hours) in data.items():
        pos = {r["id"]: k for k, r in enumerate(rows)}
        k = [pos[i] for i in ids]
        out[name] = (V[k], hours[k])
    outlet = {r["id"]: r["outlet_id"] for r in data[next(iter(models))][0]}
    return ids, np.array([outlet[i] for i in ids]), out


def sample():
    conn = store.connect()
    ids, outlets, vecs = window(conn)
    rng = np.random.default_rng(SEED)
    anchors = sorted(rng.choice(len(ids), size=N_ANCHORS, replace=False).tolist())
    pool = {}
    for name, (V, hours) in vecs.items():
        S = time_aware_similarity(V, hours, ClusterConfig())
        for a in anchors:
            s = S[a].copy()
            s[outlets == outlets[a]] = -np.inf                 # other outlets only (the judge compares across outlets)
            s[s <= -1.0] = -np.inf                              # outside the 72 h window
            for b in np.argsort(-s)[:DEPTH]:
                if np.isfinite(s[b]):
                    pool.setdefault((ids[a], ids[b]), set()).add(name)
    rows = [{"anchor": a, "candidate": b} for (a, b) in pool]
    rng.shuffle(rows)                                           # blind: order says nothing about the model
    for k, r in enumerate(rows, 1):
        r["pair"] = f"P{k:03d}"
    POOL.write_text("".join(json.dumps(r) + "\n" for r in rows))
    both = sum(1 for v in pool.values() if len(v) == 2)
    print(f"{N_ANCHORS} anchors, depth {DEPTH} per model → {len(rows)} pooled pairs ({both} found by both) → {POOL}")


def text(conn, aid: str) -> str:
    r = conn.execute("SELECT outlet_id, country, published, title, summary, full_text FROM articles WHERE id = ?",
                     (aid,)).fetchone()
    body = (r["summary"] or r["full_text"] or "").replace("\n", " ")[:320]
    return f"[{r['country']}/{r['outlet_id']} {r['published'][:16]}] {r['title']}\n      {body}"


def show(start: int, n: int):
    conn = store.connect()
    rows = [json.loads(x) for x in POOL.read_text().splitlines()][start:start + n]
    for r in rows:
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


def analyse(addendum: bool = False):
    models = MODELS_ADDENDUM if addendum else MODELS
    stem = "reports/e7b_addendum" if addendum else "reports/e7b_pooled"
    conn = store.connect()
    pool = [json.loads(x) for x in POOL.read_text().splitlines()]
    if not addendum:
        pool = [r for r in pool if "added" not in r]           # the pre-registered pool
    lab = {d["pair"]: d["same"] for d in map(json.loads, LABELS.read_text().splitlines())}
    missing = [r["pair"] for r in pool if r["pair"] not in lab]
    if missing:
        raise SystemExit(f"{len(missing)} pooled pairs have no label yet: {missing[:5]}…")
    ids, outlets, vecs = window(conn, models)
    pos = {i: k for k, i in enumerate(ids)}
    A = np.array([pos[r["anchor"]] for r in pool]); B = np.array([pos[r["candidate"]] for r in pool])
    truth = np.array([bool(lab[r["pair"]]) for r in pool])
    anchor_of = np.array([r["anchor"] for r in pool])
    anchors = sorted(set(anchor_of))
    out = {"prereg_sha256": hashlib.sha256(PREREG.read_bytes()).hexdigest(), "pairs": len(pool),
           "same": int(truth.sum()), "anchors": len(anchors),
           "anchors_with_a_match": len({a for a, t in zip(anchor_of, truth) if t}), "models": {}}

    found, sims, clus = {}, {}, {}
    for name, (V, hours) in vecs.items():
        S = time_aware_similarity(V, hours, ClusterConfig())
        sims[name] = S[A, B]
        ranked = {}
        for a in anchors:                                   # this model's own top-DEPTH for the anchor
            k = pos[a]
            s = S[k].copy()
            s[outlets == outlets[k]] = -np.inf
            s[s <= -1.0] = -np.inf
            ranked[a] = [ids[j] for j in np.argsort(-s)[:DEPTH]]
        missing = sum(1 for a in anchors for c in ranked[a] if (a, c) not in {(r["anchor"], r["candidate"]) for r in pool})
        assert missing == 0, f"{name}: {missing} of its top-{DEPTH} candidates are not labelled"
        found[name] = np.array([r["candidate"] in ranked[r["anchor"]] for r in pool])
        first = np.array([ranked[r["anchor"]][0] == r["candidate"] for r in pool])
        Z = average_linkage(V, hours, ClusterConfig(method="average"))
        clus[name] = {t: (lambda L: L[A] == L[B])(cut(Z, t)) for t in GRID + [0.60]}
        m = {"precision_at_4": round(float(truth[found[name]].mean()), 3),
             "precision_at_1": round(float(truth[first].mean()), 3),
             "recall_at_4": round(float(truth[found[name]].sum() / truth.sum()), 3) if truth.sum() else None,
             "auc": round(auc(sims[name], truth), 3),
             "sweep": [[t, *[round(x, 3) for x in prf(clus[name][t], truth)]] for t in GRID]}
        out["models"][name] = m

    ok = [(t, p, r, f) for t, p, r, f in out["models"]["OpenAI 512"]["sweep"] if p >= 0.90]
    t_star = max(ok, key=lambda x: (x[3], -x[0]))[0] if ok else None
    out["openai_threshold"] = t_star
    ops = {"MiniLM 384": 0.60, "OpenAI 512": t_star}
    if "OpenAI 1536" in models:                             # the same rule for the 1536 vectors
        ok15 = [(t, p, r, f) for t, p, r, f in out["models"]["OpenAI 1536"]["sweep"] if p >= 0.90]
        ops["OpenAI 1536"] = max(ok15, key=lambda x: (x[3], -x[0]))[0] if ok15 else None

    rng = np.random.default_rng(0)                          # bootstrap over anchors (pairs of one anchor move together)
    groups = {a: np.where(anchor_of == a)[0] for a in anchors}
    boots = {name: {"p4": [], "r4": [], "p": [], "r": [], "f": [], "auc": []} for name in models}
    for _ in range(BOOT):
        idx = np.concatenate([groups[a] for a in rng.choice(anchors, size=len(anchors), replace=True)])
        t_ = truth[idx]
        for name in models:
            boots[name]["auc"].append(auc(sims[name][idx], t_))
            f_ = found[name][idx]
            boots[name]["p4"].append(t_[f_].mean() if f_.any() else np.nan)
            boots[name]["r4"].append(t_[f_].sum() / t_.sum() if t_.sum() else np.nan)
            if ops[name] is not None:
                p, r, f = prf(clus[name][ops[name]][idx], t_)
                boots[name]["p"].append(p); boots[name]["r"].append(r); boots[name]["f"].append(f)
    for name in models:
        m = out["models"][name]
        m["precision_at_4_ci"], m["recall_at_4_ci"] = ci(boots[name]["p4"]), ci(boots[name]["r4"])
        m["auc_ci"] = ci(boots[name]["auc"])
        if ops[name] is not None:
            p, r, f = prf(clus[name][ops[name]], truth)
            m["clustering"] = {"threshold": ops[name], "precision": round(p, 3), "recall": round(r, 3), "f1": round(f, 3),
                               "precision_ci": ci(boots[name]["p"]), "recall_ci": ci(boots[name]["r"]),
                               "f1_ci": ci(boots[name]["f"])}
    if "OpenAI 1536" in models:
        out["auc_diff_1536_minus_512_ci"] = ci(np.array(boots["OpenAI 1536"]["auc"]) - np.array(boots["OpenAI 512"]["auc"]))
    Path(f"{stem}.json").write_text(json.dumps(out, indent=1))
    report(out, stem, addendum)
    print(Path(f"{stem}.md").read_text())


def report(out, stem="reports/e7b_pooled", addendum=False):
    title = ("# E7b addendum — the same test with OpenAI @1536 added at equal depth (not pre-registered)" if addendum
             else "# E7b — unbiased story-matching test: MiniLM vs OpenAI @512 (pooled, blind)")
    L = [title, "",
         f"Pre-registration `docs/experiments/e7b_prereg.json` (sha256 `{out['prereg_sha256'][:16]}…`). {out['anchors']} random "
         f"anchors, top {DEPTH} other-outlet candidates per model, {out['pairs']} pooled pairs labelled blind, "
         f"{out['same']} same story ({out['anchors_with_a_match']} anchors have at least one). 95 % intervals: "
         f"{BOOT:,} bootstrap resamples of anchors.", "",
         "| model | precision@1 | precision@4 | recall@4 (of pool) | AUC [95 %] | clustering thr | precision | recall | F1 |",
         "|---|---|---|---|---|---|---|---|---|"]
    for name, m in out["models"].items():
        c = m.get("clustering") or {}
        L.append(f"| {name} | {m['precision_at_1']} | {m['precision_at_4']} {m['precision_at_4_ci']} | {m['recall_at_4']} "
                 f"{m['recall_at_4_ci']} | {m['auc']} {m.get('auc_ci')} | {c.get('threshold')} | {c.get('precision')} {c.get('precision_ci')} | "
                 f"{c.get('recall')} {c.get('recall_ci')} | {c.get('f1')} {c.get('f1_ci')} |")
    L += ["", f"Pre-registered rule for OpenAI's story threshold (highest F1 with precision ≥ 0.90): **{out['openai_threshold']}**.",
          "", "Clustering sweep (threshold: precision / recall / F1):", ""]
    for name, m in out["models"].items():
        L.append(f"- {name}: " + " · ".join(f"{t}: {p}/{r}/{f}" for t, p, r, f in m["sweep"]))
    if "auc_diff_1536_minus_512_ci" in out:
        L += ["", f"AUC 1536 − 512, 95 % (paired, anchors): {out['auc_diff_1536_minus_512_ci']}"]
    Path(f"{stem}.md").write_text("\n".join(L) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["sample", "show", "analyse"])
    ap.add_argument("--from", dest="start", type=int, default=0)
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--addendum", action="store_true", help="include OpenAI @1536 (its candidates were added later)")
    a = ap.parse_args()
    {"sample": sample, "show": lambda: show(a.start, a.n), "analyse": lambda: analyse(a.addendum)}[a.cmd]()


if __name__ == "__main__":
    main()
