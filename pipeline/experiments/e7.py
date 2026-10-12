"""E7 analysis: OpenAI embeddings at 512 vs 1536 dimensions vs MiniLM for story matching (docs/experiments/e7_prereg.json).

    uv run python -m pipeline.experiments.e7

Free: reads the vectors the two calibration runs stored (reports/e7_calibrate_*.txt); no API calls.
Q1/Q2 (pre-registered): precision, recall and F1 of the 2.3 clustering on the 200 labelled pairs at each model's
operating point (highest recall with precision ≥ MiniLM's at 0.60, as `pipeline.embed.calibrate` picks it), 95 %
bootstrap intervals, and the paired recall difference 512 − 1536. Q3: the same for 1536 truncated to fewer dimensions.
Q4: the API's 512-d vectors vs 1536 truncated to 512. Exploratory, added after the first numbers: threshold-free AUC of
the raw cosine per labelled pair, overall and per label set (the sets were selected with MiniLM).
Writes reports/e7_embeddings.md, .json and .png. Not a claim (pipeline phase).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from pipeline import store
from pipeline.events.cluster import ClusterConfig, average_linkage, cut
from pipeline.events.run import load_window
from pipeline.settings import MINILM

PREREG = Path("docs/experiments/e7_prereg.json")
LABELS = (Path("data/eval/same_event_pairs.jsonl"), Path("data/eval/deployed_pairs_claude.jsonl"))
MODELS = {"MiniLM 384": MINILM, "OpenAI 512": "openai:text-embedding-3-small@512",
          "OpenAI 1536": "openai:text-embedding-3-small@1536"}
GRID = [round(float(t), 2) for t in np.arange(0.30, 0.91, 0.02)]
TRUNC = (64, 128, 256, 512, 768, 1024, 1536)
BOOT, SEED = 2000, 0
WINDOW_ARTICLES_30D = 48_000          # ≈ 1,600 live articles a day (A52)


def gold_pairs() -> list[dict]:
    return [json.loads(line) for f in LABELS if f.exists() for line in f.read_text().splitlines()]


def predictions(ids: list[str], labels: np.ndarray, gold: list[dict]) -> np.ndarray:
    """1 if the clustering puts the pair in one story, 0 if not, -1 if a pair's article is not in the window."""
    lab = dict(zip(ids, labels))
    return np.array([(int(lab[g["a"]] == lab[g["b"]]) if g["a"] in lab and g["b"] in lab else -1) for g in gold])


def prf(pred: np.ndarray, truth: np.ndarray) -> tuple[float, float, float]:
    keep = pred >= 0
    pred, truth = pred[keep], truth[keep]
    tp = int(((pred == 1) & truth).sum()); fp = int(((pred == 1) & ~truth).sum()); fn = int(((pred == 0) & truth).sum())
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def sweep(ids, V, hours, gold) -> dict[float, np.ndarray]:
    Z = average_linkage(V, hours, ClusterConfig(method="average"))
    return {t: predictions(ids, cut(Z, t), gold) for t in GRID}


def operating_point(preds: dict, truth: np.ndarray, base_p: float) -> float:
    """calibrate's rule: the highest recall with precision ≥ the baseline's; ties → the lower threshold."""
    stats = {t: prf(p, truth) for t, p in preds.items()}
    ok = {t: v for t, v in stats.items() if v[0] >= base_p - 1e-9}
    return max(ok, key=lambda t: (ok[t][1], -t)) if ok else max(stats, key=lambda t: stats[t][2])


def auc(scores: np.ndarray, truth: np.ndarray) -> float:
    """Probability that a same-story pair has a higher cosine than a different-story pair (Mann–Whitney)."""
    pos, neg = scores[truth], scores[~truth]
    if not len(pos) or not len(neg):
        return float("nan")
    return float(((pos[:, None] > neg[None, :]).sum() + 0.5 * (pos[:, None] == neg[None, :]).sum()) / (len(pos) * len(neg)))


def pair_cosines(ids, V, gold) -> np.ndarray:
    pos = {i: k for k, i in enumerate(ids)}
    return np.array([float(V[pos[g["a"]]] @ V[pos[g["b"]]]) if g["a"] in pos and g["b"] in pos else np.nan for g in gold])


def ci(xs) -> list[float]:
    return [round(float(np.percentile(xs, 2.5)), 3), round(float(np.percentile(xs, 97.5)), 3)]


def main():
    conn = store.connect()
    gold = gold_pairs()
    truth = np.array([bool(g["same"]) for g in gold])
    sets = np.array([g.get("set", "?") for g in gold])
    out = {"prereg_sha256": hashlib.sha256(PREREG.read_bytes()).hexdigest(), "n_pairs": len(gold),
           "n_same": int(truth.sum()), "models": {}}

    data, preds, ops = {}, {}, {}
    for name, model in MODELS.items():
        rows, V, hours = load_window(conn, 7, model)
        data[name] = ([r["id"] for r in rows], V, hours)
        preds[name] = sweep(*data[name], gold)
    base_p = prf(preds["MiniLM 384"][0.60], truth)[0]
    out["baseline_precision"] = round(base_p, 3)
    for name in MODELS:
        ops[name] = 0.60 if name == "MiniLM 384" else operating_point(preds[name], truth, base_p)

    # Q1/Q2: operating points with bootstrap intervals; paired difference 512 − 1536
    rng = np.random.default_rng(SEED)
    idx = rng.integers(0, len(gold), size=(BOOT, len(gold)))
    boot = {name: np.array([prf(preds[name][ops[name]][i], truth[i]) for i in idx]) for name in MODELS}
    for name, model in MODELS.items():
        p, r, f = prf(preds[name][ops[name]], truth)
        best_t = max(GRID, key=lambda t: prf(preds[name][t], truth)[2])
        cos = pair_cosines(*data[name][:2], gold)
        dims = data[name][1].shape[1]
        cost = conn.execute("SELECT COALESCE(SUM(cost_usd), 0), COALESCE(SUM(input_tokens), 0) FROM api_usage "
                            "WHERE kind = 'embedding' AND model = ?", (model,)).fetchone()
        out["models"][name] = {
            "model": model, "dims": dims, "threshold": ops[name], "precision": round(p, 3), "recall": round(r, 3),
            "f1": round(f, 3), "precision_ci": ci(boot[name][:, 0]), "recall_ci": ci(boot[name][:, 1]),
            "f1_ci": ci(boot[name][:, 2]), "best_f1": round(prf(preds[name][best_t], truth)[2], 3), "best_f1_threshold": best_t,
            "auc_all": round(auc(cos, truth), 3),
            "auc_by_set": {s: round(auc(cos[sets == s], truth[sets == s]), 3) for s in sorted(set(sets))},
            "recall_by_set": {s: round(prf(preds[name][ops[name]][sets == s], truth[sets == s])[1], 3) for s in sorted(set(sets))},
            "precision_by_set": {s: round(prf(preds[name][ops[name]][sets == s], truth[sets == s])[0], 3) for s in sorted(set(sets))},
            "pr_curve": [[t, *map(lambda x: round(x, 3), prf(preds[name][t], truth))] for t in GRID],
            "bytes_per_vector": dims * 4, "mb_per_30_days": round(dims * 4 * WINDOW_ARTICLES_30D / 1e6, 1),
            "calibration_cost_usd": round(cost[0], 4), "calibration_tokens": int(cost[1]),
        }
    d = boot["OpenAI 512"][:, 1] - boot["OpenAI 1536"][:, 1]
    diff = out["models"]["OpenAI 512"]["recall"] - out["models"]["OpenAI 1536"]["recall"]
    out["q2_recall_diff_512_minus_1536"] = {"diff": round(diff, 3), "ci": ci(d)}
    take_1536 = diff < -0.03 and ci(d)[1] < 0
    out["q2_decision"] = "1536" if take_1536 else "512"

    # Q3: truncated 1536 → d dimensions, re-normalised
    ids, V, hours = data["OpenAI 1536"]
    curve = []
    for k in TRUNC:
        Vk = V[:, :k] / np.linalg.norm(V[:, :k], axis=1, keepdims=True).clip(min=1e-12)
        pk = sweep(ids, Vk, hours, gold)
        t = operating_point(pk, truth, base_p)
        p, r, f = prf(pk[t], truth)
        curve.append({"dims": k, "threshold": t, "precision": round(p, 3), "recall": round(r, 3), "f1": round(f, 3),
                      "best_f1": round(max(prf(pk[x], truth)[2] for x in GRID), 3),
                      "auc_all": round(auc(pair_cosines(ids, Vk, gold), truth), 3)})
    out["q3_truncation"] = curve

    # Q4: is the API's 512-d output the 1536 vector truncated and re-normalised?
    ids5, V5, _ = data["OpenAI 512"]
    pos, pos5 = {i: k for k, i in enumerate(ids)}, {i: k for k, i in enumerate(ids5)}
    common = [i for i in ids5 if i in pos]
    A = V5[[pos5[i] for i in common]]
    T = V[[pos[i] for i in common], :512]
    T = T / np.linalg.norm(T, axis=1, keepdims=True)
    c = (A * T).sum(1)
    out["q4_api512_vs_truncated"] = {"articles": len(c), "mean_cos": round(float(c.mean()), 5), "min_cos": round(float(c.min()), 5)}

    Path("reports").mkdir(exist_ok=True)
    Path("reports/e7_embeddings.json").write_text(json.dumps(out, indent=1))
    write_report(out)
    plot(out)
    print(Path("reports/e7_embeddings.md").read_text())


def write_report(out: dict) -> None:
    m = out["models"]
    L = ["# E7 — OpenAI embeddings 512 vs 1536 vs MiniLM (story matching)", "",
         f"Pre-registration `docs/experiments/e7_prereg.json` (sha256 `{out['prereg_sha256'][:16]}…`). "
         f"{out['n_pairs']} labelled pairs ({out['n_same']} same story). Operating point: highest recall with precision ≥ "
         f"MiniLM's {out['baseline_precision']} (A47). 95 % intervals: {BOOT:,} bootstrap resamples of the pairs, "
         "thresholds held fixed.", "",
         "| model | dims | threshold | precision | recall | F1 | best F1 (thr) | AUC all | AUC band | AUC deployed | MB / 30 days | cost |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, v in m.items():
        L.append(f"| {name} | {v['dims']} | {v['threshold']} | {v['precision']} {v['precision_ci']} | {v['recall']} {v['recall_ci']} | "
                 f"{v['f1']} {v['f1_ci']} | {v['best_f1']} ({v['best_f1_threshold']}) | {v['auc_all']} | "
                 f"{v['auc_by_set'].get('band')} | {v['auc_by_set'].get('deployed')} | {v['mb_per_30_days']} | ${v['calibration_cost_usd']} |")
    q2 = out["q2_recall_diff_512_minus_1536"]
    L += ["", f"**Q2:** recall 512 − 1536 = {q2['diff']} (95 % {q2['ci']}). Pre-registered rule → **{out['q2_decision']}**.", "",
          "Recall by label set at the operating point (band = sampled around MiniLM's threshold; deployed = pairs MiniLM "
          "clustered together, so MiniLM's recall there is high by construction):", "",
          "| model | recall band | recall deployed | precision band | precision deployed |", "|---|---|---|---|---|"]
    for name, v in m.items():
        L.append(f"| {name} | {v['recall_by_set'].get('band')} | {v['recall_by_set'].get('deployed')} | "
                 f"{v['precision_by_set'].get('band')} | {v['precision_by_set'].get('deployed')} |")
    L += ["", "**Q3:** 1536 truncated and re-normalised:", "", "| dims | threshold | precision | recall | F1 | best F1 | AUC |",
          "|---|---|---|---|---|---|---|"]
    for c in out["q3_truncation"]:
        L.append(f"| {c['dims']} | {c['threshold']} | {c['precision']} | {c['recall']} | {c['f1']} | {c['best_f1']} | {c['auc_all']} |")
    q4 = out["q4_api512_vs_truncated"]
    L += ["", f"**Q4:** API 512-d vs 1536 truncated to 512: mean cosine {q4['mean_cos']}, minimum {q4['min_cos']} "
          f"over {q4['articles']:,} articles.", "", "![E7](e7_embeddings.png)", ""]
    Path("reports/e7_embeddings.md").write_text("\n".join(L))


def plot(out: dict) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.4))
    for name, v in out["models"].items():
        c = np.array([x[1:] for x in v["pr_curve"]])
        a1.plot(c[:, 1], c[:, 0], "-o", ms=2.5, label=f"{name} (AUC {v['auc_all']})")
        a1.plot(v["recall"], v["precision"], "k*", ms=9)
    a1.axhline(out["baseline_precision"], ls=":", c="grey", lw=1)
    a1.set_xlabel("recall (labelled same-story pairs found)"); a1.set_ylabel("precision")
    a1.set_title("2.3 clustering over the threshold sweep (★ = operating point)", fontsize=9); a1.legend(fontsize=8)
    tr = out["q3_truncation"]
    a2.plot([c["dims"] for c in tr], [c["recall"] for c in tr], "-o", label="recall at MiniLM's precision")
    a2.plot([c["dims"] for c in tr], [c["best_f1"] for c in tr], "-s", label="best F1 over the sweep")
    a2.plot([c["dims"] for c in tr], [c["auc_all"] for c in tr], "-^", label="pair AUC (threshold-free)")
    a2.set_xscale("log", base=2); a2.set_xticks([c["dims"] for c in tr], [str(c["dims"]) for c in tr])
    a2.set_xlabel("dimensions kept (1536 truncated, re-normalised)"); a2.set_ylim(0, 1.02)
    a2.set_title("Q3: how much of the vector is needed", fontsize=9); a2.legend(fontsize=8)
    fig.tight_layout(); fig.savefig("reports/e7_embeddings.png", dpi=120); plt.close(fig)


if __name__ == "__main__":
    main()
