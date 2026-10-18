"""E8 (docs/experiments/e8_prereg.json) and the PC's text embedder (infra/pc/embed_texts.py): chunked, resumable
embedding; the export → GPU → import round trip; the pooled sample; the analysis and its pre-registered rule —
on a synthetic window with known stories, so every number has an expected value."""
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

from pipeline import store
from pipeline.experiments import e8

spec = importlib.util.spec_from_file_location("embed_texts", Path(__file__).parent.parent / "infra/pc/embed_texts.py")
embed_texts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(embed_texts)


class FakeModel:
    """sentence-transformers' interface; a vector is a hash of the text, so the same text gives the same row."""

    def __init__(self, dim=8):
        self.dim, self.seen = dim, 0

    def get_sentence_embedding_dimension(self):
        return self.dim

    def encode(self, texts, batch_size=32, normalize_embeddings=True, convert_to_numpy=True):
        self.seen += len(texts)
        out = np.array([np.frombuffer(hashlib.sha256(t.encode()).digest()[:self.dim * 4], dtype=np.uint32)
                        .astype(np.float64) for t in texts])
        return out / np.linalg.norm(out, axis=1, keepdims=True)


def write_texts(path, n):
    path.write_text("".join(json.dumps({"t": f"text {k}"}) + "\n" for k in range(n)))


def test_embed_file_in_chunks_equals_one_pass(tmp_path):
    write_texts(tmp_path / "t.jsonl", 10)
    assert embed_texts.embed_file(FakeModel(), tmp_path / "t.jsonl", tmp_path / "a.npy", chunk=3) == 10
    assert embed_texts.embed_file(FakeModel(), tmp_path / "t.jsonl", tmp_path / "b.npy", chunk=100) == 10
    a, b = np.load(tmp_path / "a.npy"), np.load(tmp_path / "b.npy")
    assert a.shape == (10, 8) and np.allclose(a, b) and np.allclose(np.linalg.norm(a, axis=1), 1)


def test_embed_file_resumes_after_the_last_finished_chunk(tmp_path):
    write_texts(tmp_path / "t.jsonl", 10)
    embed_texts.embed_file(FakeModel(), tmp_path / "t.jsonl", tmp_path / "v.npy", chunk=3)
    full = np.load(tmp_path / "v.npy").copy()
    m = np.lib.format.open_memmap(tmp_path / "v.npy", mode="r+")
    m[6:] = 0                                              # a run that stopped after two chunks
    m.flush()
    (tmp_path / "v.npy.done").write_text("6")
    model = FakeModel()
    assert embed_texts.embed_file(model, tmp_path / "t.jsonl", tmp_path / "v.npy", chunk=3) == 10
    assert model.seen == 4 and np.allclose(np.load(tmp_path / "v.npy"), full)


def test_mean_per_article_normalises_and_checks_the_count():
    E = np.array([[1.0, 0.0], [0.0, 1.0], [0.0, 2.0]])
    A = e8.mean_per_article(E, [2, 1])
    assert np.allclose(A, [[2 ** -0.5, 2 ** -0.5], [0.0, 1.0]])
    with pytest.raises(SystemExit):
        e8.mean_per_article(E, [1, 1])


def test_pair_agreement_counts_pairs():
    same = np.array([0, 0, 1, 1])
    assert e8.pair_agreement(same, same) == (1.0, 1.0)
    p, r = e8.pair_agreement(np.array([0, 0, 0, 1]), same)    # mine: (0,1),(0,2),(1,2); theirs: (0,1),(2,3)
    assert p == pytest.approx(1 / 3) and r == pytest.approx(1 / 2)


STORIES, PER_STORY, OUTLETS = 15, 4, ["o1", "o2", "o3", "o4", "o5", "o6"]


@pytest.fixture
def window(tmp_path, monkeypatch):
    """A 7-day window of 60 articles in 15 stories (4 outlets each); OpenAI vectors stored in scrambled order, and
    three local models that see the stories with more or less noise."""
    monkeypatch.chdir(tmp_path)
    for name, value in (("DB", tmp_path / "w.sqlite"), ("VECS", tmp_path / "vecs"), ("POOL", tmp_path / "pool.jsonl"),
                        ("LABELS", tmp_path / "labels.jsonl"), ("PREREG", tmp_path / "prereg.json")):
        monkeypatch.setattr(e8, name, value)
    (tmp_path / "prereg.json").write_text("{}")
    rng = np.random.default_rng(0)
    centres = rng.normal(size=(STORIES, 16))
    conn = store.connect(e8.DB)
    ids, story, base = [], {}, []
    for s in range(STORIES):
        for k in range(PER_STORY):
            aid = f"a{s:02d}{k}"
            hour = 10 + 10 * s + k                          # stories spread over the week, each within a few hours
            conn.execute("INSERT INTO articles (id, outlet_id, country, url, title, published, summary) VALUES (?,?,?,?,?,?,?)",
                         (aid, OUTLETS[(s + k) % 6], "SE", f"https://x/{aid}", f"Story {s} take {k}",
                          f"2026-10-{10 + hour // 24:02d}T{hour % 24:02d}:00:00Z", "summary"))
            ids.append(aid); story[aid] = s; base.append(centres[s] + 0.3 * rng.normal(size=16))
    V = np.array(base); V /= np.linalg.norm(V, axis=1, keepdims=True)
    order = rng.permutation(len(ids))                      # the table's order is not the window's
    store.save_embeddings(conn, "openai:text-embedding-3-small@512", [ids[k] for k in order], V[order])
    conn.commit()
    rows = e8.window(conn)
    e8.VECS.mkdir()
    for name, noise in zip(e8.LOCAL, (0.2, 0.6, 1.5)):
        L = np.array([V[ids.index(r["id"])] + noise * rng.normal(size=16) for r in rows])
        e8.save(name, rows, L / np.linalg.norm(L, axis=1, keepdims=True))
    return conn, rows, story, V, ids


def test_vectors_follow_the_window_order_not_the_tables(window):
    conn, rows, _, V, ids = window
    got = e8.vectors(conn, rows)[e8.REFERENCE]
    assert np.allclose(got, V[[ids.index(r["id"]) for r in rows]], atol=1e-6)


def test_export_and_import_round_trip(window, tmp_path):
    conn, rows, *_ = window
    e8.export()
    lines = (e8.VECS / "texts_Qwen3-Embedding-0.6B.jsonl").read_text().splitlines()
    assert len(lines) == len(rows) and json.loads(lines[0])["t"].startswith(e8.INSTRUCT)          # instruction model
    assert not json.loads((e8.VECS / "texts_bge-m3.jsonl").read_text().splitlines()[0])["t"].startswith("Instruct")
    embed_texts.embed_file(FakeModel(), e8.VECS / "texts_bge-m3.jsonl", tmp_path / "bge.npy")
    e8.import_vectors("bge-m3", tmp_path / "bge.npy")
    assert np.allclose(np.load(e8.VECS / "bge-m3.npy"), e8.mean_per_article(np.load(tmp_path / "bge.npy"), [1] * len(rows)))


def test_sample_pools_other_outlet_candidates_from_every_model(window):
    conn, rows, *_ = window
    e8.sample()
    pool = [json.loads(x) for x in e8.POOL.read_text().splitlines()]
    outlet = {r["id"]: r["outlet_id"] for r in rows}
    assert pool and all(outlet[p["anchor"]] != outlet[p["candidate"]] for p in pool)
    assert len({p["anchor"] for p in pool}) == e8.N_ANCHORS
    assert len(pool) <= e8.N_ANCHORS * e8.DEPTH * (len(e8.LOCAL) + 1)
    assert [p["pair"] for p in pool] == [f"Q{k:03d}" for k in range(1, len(pool) + 1)]


def test_analyse_on_known_stories_picks_the_cleanest_model(window):
    conn, rows, story, *_ = window
    e8.sample()
    pool = [json.loads(x) for x in e8.POOL.read_text().splitlines()]
    e8.LABELS.write_text("".join(json.dumps({"pair": p["pair"], "same": story[p["anchor"]] == story[p["candidate"]]}) + "\n"
                                 for p in pool))
    e8.analyse()
    out = json.loads(Path("reports/e8.json").read_text())
    m = out["models"]
    assert set(m) == {e8.REFERENCE, *e8.LOCAL} and all(0 <= x["auc"] <= 1 for x in m.values())
    assert m["Qwen3-Embedding-0.6B"]["auc"] >= m["e5-large-instruct"]["auc"]     # less noise, better separation
    assert out["decision"]["history_model"] in e8.LOCAL
    best = m[out["decision"]["history_model"]]
    if out["decision"]["threshold"] is not None:
        assert "M4_f1_minus_live_ci" in best and len(best["M3_like_live"]) == 2
