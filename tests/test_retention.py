"""A52 retention and 4.2 export on a small synthetic database: the dry run changes nothing; --apply archives first,
deletes only what is outside the window, and a refit from the kept verdicts reproduces the stored positions.
The export holds only aggregates and fits the payload budget. No network, no key, no spend."""
import datetime as dt
import json
import sys
import uuid

import numpy as np
import pyarrow.parquet as pq
import pytest

from pipeline import store
from pipeline.export import run as export
from pipeline.judge import calllog
from pipeline.judge import prompt as P
from pipeline.retention import run as R
from pipeline.sources.registry import load_outlets, reference_groups

NOW = dt.datetime.now(dt.UTC)


def iso(days_ago: float) -> str:
    return (NOW - dt.timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def month(days_ago: float) -> str:
    return iso(days_ago)[:7]


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("BASEDNEWS_DB", str(tmp_path / "db.sqlite"))
    monkeypatch.setenv("BASEDNEWS_LAKE", str(tmp_path / "lake"))
    monkeypatch.setenv("BASEDNEWS_STATE", "s3://not-used")           # no local backup copy in tests
    conn = store.connect()
    arts = [("old-live", 60, "trafilatura"), ("old-archive", 60, "wayback-trafilatura"), ("new-live", 2, "trafilatura")]
    for aid, age, src in arts:
        conn.execute("INSERT INTO articles (id, outlet_id, country, url, title, published, summary, full_text, ingested_at,"
                     " text_source) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (aid, "nrk", "Norway", f"https://x/{aid}", f"T {aid}", iso(age), "summary", "full text " * 50,
                      iso(age), src))
        store.save_embeddings(conn, "m", [aid], np.ones((1, 4), dtype=np.float32))
    for k, run in enumerate(("2026-10-01T00:00:00Z_a", "2026-10-02T00:00:00Z_a", "2026-10-03T00:00:00Z_a")):
        store.save_events(conn, run, {"new-live": f"ev{k}"})
    seed_verdicts(conn)
    log = {"request": {"model": "m", "messages": [{"role": "user", "content": "article text"}]}, "started_at": iso(45),
           "response": {"model": "m"}, "attempts": 1}
    calllog.save(conn, "old-call", log, {"run_id": "r"})
    calllog.save(conn, "new-call", {**log, "started_at": iso(1)}, {"run_id": "r"})
    conn.commit()
    return conn


def seed_verdicts(conn, n=240, seed=0):
    """Real outlet ids, random verdicts, stored the way the measure loop stores them, then positions saved like it."""
    from pipeline.measure.run import fit
    rng = np.random.default_rng(seed)
    ids = sorted({o for g in reference_groups(load_outlets()).values() for o in g})
    for i in range(n):
        a, b = rng.choice(ids, 2, replace=False)
        cid = uuid.uuid4().hex
        conn.execute("INSERT INTO judge_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (cid, iso(3), "measure", "gpt-test", P.PROMPT_VERSION, "positive", f"ev{i}:full", f"{a}-{i}",
                      f"{b}-{i}", a, b, "full", 100, 10, 0.001, "{}"))
        for ax in P.AXES:
            conn.execute("INSERT INTO judgments VALUES (?,?,?,?,?)", (cid, ax, "A", int(rng.random() < 0.5), ""))
    posts, _ = fit(conn, ids)
    conn.executemany("INSERT INTO positions VALUES (?,?,?,?,?,?,?)",
                     [(iso(2.9), P.PROMPT_VERSION, o, ax, float(posts[ax].outlet_posterior().mean[k]), 0.1, 1)
                      for ax in P.AXES for k, o in enumerate(ids)])


def run_main(monkeypatch, module, *args):
    monkeypatch.setattr(sys, "argv", ["x", *args])
    module.main()


def test_dry_run_changes_nothing(db, monkeypatch, tmp_path):
    before = db.execute("SELECT COUNT(*), SUM(full_text IS NOT NULL) FROM articles").fetchone()
    run_main(monkeypatch, R)
    db2 = store.connect()
    assert db2.execute("SELECT COUNT(*), SUM(full_text IS NOT NULL) FROM articles").fetchone() == before
    assert db2.execute("SELECT COUNT(*) FROM lake_files").fetchone()[0] == 0 and not (tmp_path / "lake").exists()


def test_apply_archives_then_deletes_only_outside_the_window(db, monkeypatch, tmp_path):
    run_main(monkeypatch, R, "--apply")
    c = store.connect()
    text = {r[0]: (r[1], r[2]) for r in c.execute("SELECT id, full_text IS NOT NULL, pruned_at FROM articles")}
    assert text["old-live"][0] == 0 and text["old-live"][1]                   # text gone, marked
    assert text["old-archive"][0] == 1 and text["new-live"][0] == 1             # archive sample and window kept
    assert {r[0] for r in c.execute("SELECT article_id FROM embeddings")} == {"old-archive", "new-live"}
    assert [r[0] for r in c.execute("SELECT DISTINCT run_id FROM event_members ORDER BY 1")] == \
           ["2026-10-02T00:00:00Z_a", "2026-10-03T00:00:00Z_a"]
    old, new = calllog.load(c, "old-call"), calllog.load(c, "new-call")
    assert old["request_archived"] and old["request"] is None and old["request_sha256"]
    assert new["request"]["messages"][0]["content"] == "article text"          # inside the window: untouched
    lake = tmp_path / "lake"
    t = pq.read_table(lake / "judge_call_log" / f"month={month(45)}" / "part.parquet")
    assert t.column("call_id").to_pylist() == ["old-call"] and t.column("request_z").to_pylist()[0]
    assert pq.read_table(lake / "judge_calls" / f"month={month(3)}" / "part.parquet").num_rows == 240
    assert pq.read_table(lake / "judgments" / f"month={month(3)}" / "part.parquet").num_rows == 480
    meta = pq.read_table(lake / "articles_meta" / f"month={month(60)}" / "part.parquet")
    assert "full_text" not in meta.column_names and "summary" not in meta.column_names
    assert R.lake_plan(c) == []                                                 # a second run has nothing to write
    v = R.verify(c)
    assert v["integrity"] == "ok" and v["refit_max_abs_diff"] < 1e-9           # A52's eval: refit reproduces


def test_export_has_aggregates_only_and_fits_the_budget(db, tmp_path):
    data = export.build(db)
    countries = set(reference_groups(load_outlets()))
    assert {c["id"] for c in data["frame"]["countries"]} == countries and data["meta"]["validated"] is False
    path = tmp_path / "out" / export.FILE
    path.parent.mkdir()
    from pipeline.aggregate.plane import dumps_site
    path.write_text(dumps_site(data))
    res = export.check(path)
    assert res["ok"] and res["gzip_bytes"] < export.BUDGET_GZIP_BYTES
    blob = json.dumps(data)
    for leak in ("full text", "summary", "https://", "T old-live"):            # A10: nothing from the articles
        assert leak not in blob
