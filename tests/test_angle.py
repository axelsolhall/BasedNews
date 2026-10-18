"""A70 (pipeline.plan.angle): a pair's chance of an answer per axis, from its mean article vector. It must find a
planted direction, stay out of the way below MIN_LABELS, read labels from the judge's own verdicts, and change only
the relevance of candidates that have vectors."""
import numpy as np

from pipeline import store
from pipeline.judge import prompt as P
from pipeline.plan import angle as AN
from pipeline.plan.planner import Candidate


def planted(n=3000, dim=32, seed=0):
    rng = np.random.default_rng(seed)
    V = rng.normal(size=(n, dim)); V /= np.linalg.norm(V, axis=1, keepdims=True)
    u = np.zeros(dim); u[0] = 1.0                          # econ angle along one direction, social along another
    z = np.stack([V @ u * 12 - 2.5, V[:, 1] * 6], axis=1)
    Y = (rng.random((n, 2)) < 1 / (1 + np.exp(-z))).astype(float)
    return V, Y, [f"s{k // 3}" for k in range(n)]           # three pairs per story


def test_it_finds_a_planted_direction_out_of_fold():
    X, Y, groups = planted()
    pr = AN.cross_validated(X, Y, groups)
    assert AN.auc(Y[:, 0], pr[:, 0]) > 0.8 and AN.auc(Y[:, 1], pr[:, 1]) > 0.65
    top = np.argsort(-pr[:, 0])[: len(Y) // 5]
    assert Y[top, 0].mean() > 2 * Y[:, 0].mean()            # the top fifth is answered far more often
    assert pr.min() >= AN.FLOOR and pr.max() <= AN.CEIL
    assert abs(AN.auc(np.array([0, 0, 1, 1.0]), np.array([0.1, 0.2, 0.3, 0.4])) - 1.0) < 1e-12


def test_labels_cold_start_and_apply(tmp_path):
    conn = store.connect(tmp_path / "a.sqlite")
    X, Y, _ = planted(n=400)
    ids = [f"a{k}" for k in range(2 * len(X))]
    store.save_embeddings(conn, "m", ids, np.repeat(X, 2, axis=0).astype(np.float32))   # both articles: the pair's vector
    for k in range(len(X)):
        conn.execute("INSERT INTO judge_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (f"c{k}", f"2016-01-01T00:{k // 60:02d}:{k % 60:02d}Z", "history", "judge", P.PROMPT_VERSION,
                      "positive", f"d:ev{k}:summary", f"a{2 * k}", f"a{2 * k + 1}", "x", "y", "summary", 0, 0, 0.0, ""))
        for j, ax in enumerate(P.AXES):
            v = "A" if Y[k, j] else "na"
            conn.execute("INSERT INTO judgments VALUES (?,?,?,?,?)", (f"c{k}", ax, v, P.first_wins(v), None))
    conn.commit()
    vecs = lambda i: store.load_embeddings(conn, "m", i)      # noqa: E731
    rows = AN.labels(conn, "history")
    assert len(rows) == 400 and rows[0]["story"] == "d:ev399"                        # newest first, kind dropped
    assert [r["answered"]["econ"] for r in rows[::-1]] == [bool(y) for y in Y[:, 0]]
    assert len(AN.labels(conn, "history", limit=50)) == 50
    assert AN.learn(conn, "nobody", vecs) is None                                    # cold start: no model
    model = AN.learn(conn, "history", vecs)
    assert model is not None and model.n_labels == 400
    cands = [Candidate("e1", "x", "y", "a0", "a1", relevance=(0.14, 0.5)),
             Candidate("e2", "x", "y", "a0", "missing", relevance=(0.14, 0.5))]
    out = AN.apply(cands, model, vecs)
    assert out[0].relevance != (0.14, 0.5) and len(out[0].relevance) == 2
    assert out[1].relevance == (0.14, 0.5)                                           # no vector: the average stays
    assert AN.apply(cands, None, vecs) == cands


def test_live_judge_step_uses_its_own_verdicts(tmp_path):
    """measure.run.with_angle: live's candidates get their own chance of an answer once live has enough verdicts."""
    from pipeline.measure.run import with_angle
    conn = store.connect(tmp_path / "live.sqlite")
    X, Y, _ = planted(n=400)
    store.save_embeddings(conn, "emb", [f"a{k}" for k in range(800)], np.repeat(X, 2, axis=0).astype(np.float32))
    cands = [Candidate("e1:summary", "x", "y", "a0", "a1", relevance=(0.14, 0.5))]
    assert with_angle(conn, cands, "measure", "emb") == (cands, 0)                  # no verdicts yet: the average
    for k in range(400):
        conn.execute("INSERT INTO judge_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (f"c{k}", f"2026-10-1{k % 9}T00:00:00Z", "measure", "gpt-5.4-mini", P.PROMPT_VERSION, "positive",
                      f"ev{k}:summary", f"a{2 * k}", f"a{2 * k + 1}", "x", "y", "summary", 0, 0, 0.0, ""))
        for j, ax in enumerate(P.AXES):
            v = "A" if Y[k, j] else "na"
            conn.execute("INSERT INTO judgments VALUES (?,?,?,?,?)", (f"c{k}", ax, v, P.first_wins(v), None))
    conn.commit()
    out, taught = with_angle(conn, cands, "measure", "emb")
    assert taught == 400 and out[0].relevance != (0.14, 0.5)
