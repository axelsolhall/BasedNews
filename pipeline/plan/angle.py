"""Per-pair angle (A70): the chance that the judge finds an economic / social angle in a pair, predicted from the pair's
mean article vector — so the planner weighs each candidate by its own chance of an answer (A32) instead of one average
per axis. The judge says "no angle" on the economic axis in ≈ 6 of 7 pairs (a crime story, a football result); a call
on such a pair costs as much and adds nothing on that axis.

    PYTHONPATH=. uv run python -m pipeline.plan.angle eval [--db data/basednews.sqlite] [--purpose measure] \\
        [--model openai:text-embedding-3-small@512]

Input: verdicts (answered or "na" per axis) and the vectors of their articles. Output: an AngleModel whose `predict`
gives P(answer) per axis for a pair's mean vector; `apply` returns candidates with that as their relevance. One L2
logistic regression per axis (numpy + scipy, no new dependency), trained on the judge's own verdicts — the history's
on the history's vectors, since live's vectors are another model's. Below MIN_LABELS verdicts there is no model and
the planner keeps the average (the cold start). `eval` cross-validates by story (a story never in train and test).
"""
from __future__ import annotations

import argparse
import dataclasses
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize

from pipeline.judge import prompt as P

MIN_LABELS = 300                # verdicts before a model is trusted over the average (A70)
MAX_LABELS = 20_000             # the most recent ones, at most: the fit stays a second or two
L2 = 1.0                        # ridge on the weights (vectors are unit length: one scale for every model)
FLOOR, CEIL = 0.02, 0.98        # a pair is never written off, nor taken as certain
Vectors = Callable[[list[str]], tuple[list[str], np.ndarray]]


@dataclass
class AngleModel:
    axes: tuple[str, ...]
    w: np.ndarray                   # axes × dim
    b: np.ndarray                   # axes
    n_labels: int

    def predict(self, X: np.ndarray) -> np.ndarray:
        """pairs × axes: P(the judge answers the axis) for each pair's mean vector."""
        return np.clip(1 / (1 + np.exp(-(X @ self.w.T + self.b))), FLOOR, CEIL)


def _logistic(X: np.ndarray, y: np.ndarray, l2: float) -> tuple[np.ndarray, float]:
    def loss(theta):
        w, b = theta[:-1], theta[-1]
        z = X @ w + b
        p = 1 / (1 + np.exp(-z))
        nll = np.sum(np.logaddexp(0, z) - y * z) + 0.5 * l2 * w @ w
        g = X.T @ (p - y) + l2 * w
        return nll, np.concatenate([g, [np.sum(p - y)]])
    rate = np.clip(y.mean(), 1e-3, 1 - 1e-3)
    theta0 = np.concatenate([np.zeros(X.shape[1]), [np.log(rate / (1 - rate))]])
    res = minimize(loss, theta0, jac=True, method="L-BFGS-B", options={"maxiter": 500})
    return res.x[:-1], float(res.x[-1])


def fit(X: np.ndarray, Y: np.ndarray, axes=P.AXES, l2: float = L2) -> AngleModel:
    """X: pairs × dim (mean article vectors); Y: pairs × axes, 1 = the judge answered the axis."""
    ws, bs = zip(*(_logistic(X, Y[:, k].astype(float), l2) for k in range(len(axes))))
    return AngleModel(tuple(axes), np.array(ws), np.array(bs), len(X))


def labels(conn, purpose: str, version: str = P.PROMPT_VERSION, limit: int = MAX_LABELS) -> list[dict]:
    """The most recent verdicts of a purpose: the pair, its story and whether each axis was answered."""
    rows = conn.execute("""SELECT c.call_id, c.event_id, c.first_article a, c.second_article b, j.axis, j.verdict
                           FROM judgments j JOIN judge_calls c ON c.call_id = j.call_id
                           WHERE c.purpose = ? AND c.prompt_version = ?
                           ORDER BY c.at DESC, c.call_id""", (purpose, version)).fetchall()
    calls: dict[str, dict] = {}
    for r in rows:
        c = calls.setdefault(r["call_id"], {"a": r["a"], "b": r["b"], "story": (r["event_id"] or r["call_id"]).rsplit(":", 1)[0],
                                            "answered": {}})
        c["answered"][r["axis"]] = r["verdict"] != "na"
        if len(calls) > limit:
            calls.pop(r["call_id"])
            break
    return [c for c in calls.values() if all(ax in c["answered"] for ax in P.AXES)]


def pair_vectors(pairs: list[tuple[str, str]], vectors: Vectors) -> tuple[np.ndarray, np.ndarray]:
    """Mean of the two articles' vectors per pair, and which pairs had both vectors."""
    ids = sorted({a for p in pairs for a in p})
    got, V = vectors(ids)
    pos = {a: k for k, a in enumerate(got)}
    ok = np.array([a in pos and b in pos for a, b in pairs], dtype=bool)
    if not ok.any():
        return np.zeros((0, V.shape[1] if V.ndim == 2 else 0)), ok
    X = np.array([(V[pos[a]] + V[pos[b]]) / 2 for (a, b), k in zip(pairs, ok) if k], dtype=float)
    return X, ok


def training_set(conn, purpose: str, vectors: Vectors) -> tuple[np.ndarray, np.ndarray, list[str]]:
    rows = labels(conn, purpose)
    X, ok = pair_vectors([(r["a"], r["b"]) for r in rows], vectors)
    rows = [r for r, k in zip(rows, ok) if k]
    Y = np.array([[r["answered"][ax] for ax in P.AXES] for r in rows], dtype=float).reshape(len(rows), len(P.AXES))
    return X, Y, [r["story"] for r in rows]


def learn(conn, purpose: str, vectors: Vectors) -> AngleModel | None:
    """A model from the purpose's own verdicts, or None below MIN_LABELS (the planner keeps the average)."""
    X, Y, _ = training_set(conn, purpose, vectors)
    return fit(X, Y) if len(X) >= MIN_LABELS else None


def apply(cands: list, model: AngleModel | None, vectors: Vectors) -> list:
    """Candidates with relevance = their own predicted chance of an answer per axis (unchanged without a model)."""
    if model is None or not cands:
        return cands
    X, ok = pair_vectors([(c.article_a, c.article_b) for c in cands], vectors)
    probs = iter(model.predict(X)) if len(X) else iter(())
    return [dataclasses.replace(c, relevance=tuple(float(v) for v in next(probs))) if k else c
            for c, k in zip(cands, ok)]


# ---- eval ---------------------------------------------------------------------------------------------------------
def auc(y: np.ndarray, p: np.ndarray) -> float:
    """Area under the ROC curve (Mann–Whitney, ties averaged)."""
    from scipy.stats import rankdata
    pos = y.astype(bool)
    if pos.all() or not pos.any():
        return float("nan")
    r = rankdata(p)
    return float((r[pos].sum() - pos.sum() * (pos.sum() + 1) / 2) / (pos.sum() * (~pos).sum()))


def cross_validated(X: np.ndarray, Y: np.ndarray, groups: list[str], folds: int = 5, seed: int = 0) -> np.ndarray:
    """Out-of-fold predictions, folds by story."""
    stories = sorted(set(groups))
    fold_of = dict(zip(np.random.default_rng(seed).permutation(stories), np.arange(len(stories)) % folds))
    f = np.array([fold_of[g] for g in groups])
    out = np.zeros_like(Y, dtype=float)
    for k in range(folds):
        tr, te = f != k, f == k
        if te.any() and tr.any():
            out[te] = fit(X[tr], Y[tr]).predict(X[te])
    return out


def report(Y: np.ndarray, Pr: np.ndarray, shares=(0.1, 0.2, 0.3, 0.5)) -> str:
    lines = []
    for k, ax in enumerate(P.AXES):
        y, p = Y[:, k], Pr[:, k]
        order = np.argsort(-p)
        top = ", ".join(f"{int(s * 100)} % → {y[order[: max(1, int(s * len(y)))]].mean():.0%}" for s in shares)
        lines.append(f"{ax}: {len(y):,} calls, answered {y.mean():.0%}; AUC {auc(y, p):.2f}; answered among the "
                     f"top-scored {top}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["eval"])
    ap.add_argument("--db", default="data/basednews.sqlite")
    ap.add_argument("--purpose", default="measure")
    ap.add_argument("--version", default=None, help="prompt versions, comma-separated (default: the current one)")
    ap.add_argument("--model", default="openai:text-embedding-3-small@512", help="the vectors' model in the database")
    args = ap.parse_args()
    from pipeline import store
    conn = store.connect(args.db)
    vecs: Vectors = lambda ids: store.load_embeddings(conn, args.model, ids)   # noqa: E731
    rows = []
    for v in (args.version or P.PROMPT_VERSION).split(","):
        rows += labels(conn, args.purpose, v)
    X, ok = pair_vectors([(r["a"], r["b"]) for r in rows], vecs)
    rows = [r for r, k in zip(rows, ok) if k]
    Y = np.array([[r["answered"][ax] for ax in P.AXES] for r in rows], dtype=float)
    print(f"{len(rows):,} {args.purpose} calls with both vectors ({args.model}); 5-fold by story")
    print(report(Y, cross_validated(X, Y, [r["story"] for r in rows])))


if __name__ == "__main__":
    main()
