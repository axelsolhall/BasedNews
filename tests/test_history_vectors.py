"""History vectors outside the database (pipeline.history.vectors): export → the PC's embedder (float16) → index →
the replay reads its window from the file and groups exactly as from vectors in the database."""
import datetime as dt
import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from pipeline import store
from pipeline.history import replay
from pipeline.history import vectors as hv

spec = importlib.util.spec_from_file_location("embed_texts", Path(__file__).parent.parent / "infra/pc/embed_texts.py")
embed_texts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(embed_texts)

DAY = dt.date(2017, 5, 4)


class StoryModel:
    """Vectors near a centre per story ("Story <s> …" in the text), so grouping has a right answer."""

    def __init__(self):
        rng = np.random.default_rng(0)
        self.centres = rng.normal(size=(10, 32))

    def get_sentence_embedding_dimension(self):
        return 32

    def encode(self, texts, batch_size=32, normalize_embeddings=True, convert_to_numpy=True):
        out = []
        for t in texts:
            s = int(t.split("Story ")[1].split()[0])
            noise = np.frombuffer(hashlib.sha256(t.encode()).digest(), dtype=np.uint8)[:32] / 255.0 - 0.5
            out.append(self.centres[s] + 0.1 * noise)
        out = np.array(out)
        return out / np.linalg.norm(out, axis=1, keepdims=True)


def history_db(path):
    c = replay.prepare(store.connect(path))
    for s in range(10):
        for k in range(3):
            aid = f"s{s}k{k}"
            c.execute("INSERT INTO articles (id, outlet_id, country, url, title, published, summary) VALUES (?,?,?,?,?,?,?)",
                      (aid, ["nrk", "svt", "dn"][k], "x", f"https://x/{aid}", f"Story {s} take {k}",
                       f"{(DAY - dt.timedelta(days=s % 3)).isoformat()}T{10 + k}:00:00Z", "what happened"))
    c.commit()
    return c


@pytest.fixture
def exported(tmp_path):
    conn = history_db(tmp_path / "h.sqlite")
    out = tmp_path / "vec"
    assert hv.export(conn, out) == 30
    embed_texts.embed_file(StoryModel(), out / "texts.jsonl", out / "vectors.npy", chunk=7, dtype="float16")
    return conn, out


def test_export_writes_texts_and_ids_in_publication_order(tmp_path):
    conn = history_db(tmp_path / "h.sqlite")
    hv.export(conn, tmp_path / "v", prefix="Instruct: x\nQuery: ")
    ids = (tmp_path / "v/ids.txt").read_text().split()
    published = [conn.execute("SELECT published FROM articles WHERE id = ?", (i,)).fetchone()[0] for i in ids]
    assert published == sorted(published)
    first = json.loads((tmp_path / "v/texts.jsonl").read_text().splitlines()[0])["t"]
    assert first.startswith("Instruct: x\nQuery: Story ") and first.endswith("what happened")


def test_vectors_come_back_in_the_order_asked(exported):
    conn, out = exported
    assert hv.import_index(conn, "story-model", out) == 30
    V = np.load(out / "vectors.npy")
    assert V.dtype == np.float16
    ids = (out / "ids.txt").read_text().split()
    vf = hv.VectorFile(out, "story-model")
    ask = [ids[7], "missing", ids[2], ids[29]]
    found, M = vf.rows_for(conn, ask)
    assert found == [ids[7], ids[2], ids[29]] and M.dtype == np.float32
    assert np.allclose(M, V[[7, 2, 29]].astype(np.float32))


def test_grouping_from_the_file_equals_grouping_from_the_database(exported, tmp_path):
    conn, out = exported
    hv.import_index(conn, "story-model", out)
    ids = (out / "ids.txt").read_text().split()
    V = np.load(out / "vectors.npy").astype(np.float32)
    other = history_db(tmp_path / "db.sqlite")
    store.save_embeddings(other, "story-model", ids, V)
    other.commit()
    a = replay.regroup(conn, DAY, "story-model", 0.8, vectors=hv.VectorFile(out, "story-model"))
    b = replay.regroup(other, DAY, "story-model", 0.8)
    members = lambda c, run: {frozenset(r[0] for r in c.execute("SELECT article_id FROM event_members WHERE run_id = ? "   # noqa: E731
                                                              "AND event_id = ?", (run, e)))
                              for (e,) in c.execute("SELECT DISTINCT event_id FROM event_members WHERE run_id = ?", (run,))}
    assert a == b and members(conn, a[0]) == members(other, b[0])
    assert a[2] == 10                                          # the ten stories, three outlets each


def test_import_refuses_an_unfinished_or_mismatched_file(exported):
    conn, out = exported
    (Path(str(out / "vectors.npy") + ".done")).write_text("14")
    with pytest.raises(SystemExit, match="stopped at 14"):
        hv.import_index(conn, "m", out)
    (Path(str(out / "vectors.npy") + ".done")).write_text("30")
    shutil.copy(out / "ids.txt", out / "ids.bak")
    (out / "ids.txt").write_text("\n".join((out / "ids.bak").read_text().split()[:-1]))
    with pytest.raises(SystemExit, match="29 ids but 30 vectors"):
        hv.import_index(conn, "m", out)
