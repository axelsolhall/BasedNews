"""A53 gates: each job step's output goes on only if a few eval numbers are in range. One passing and one failing
case per gate, on a synthetic database (thresholds from pipeline/job/gates.py). No network, no spend."""
import datetime as dt

import numpy as np
import pytest

from pipeline import store
from pipeline.contracts import FeedHealth
from pipeline.job import gates
from pipeline.job.runner import snapshot
from pipeline.sources.registry import load_outlets

NOW = dt.datetime.now(dt.UTC)
OUTLETS = [o.id for o in load_outlets()]


def ago(hours):
    return (NOW - dt.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def ctx(conn, before=None, delta=None, started_at=None, budget=None):
    after = snapshot(conn)
    return {"started_at": started_at or ago(0.5), "before": before or after, "after": after, "delta": delta or {},
            "budget_usd": budget}


@pytest.fixture
def conn():
    return store.connect(":memory:")


def add_article(conn, aid, outlet, hours_ago, text_source="trafilatura"):
    conn.execute("INSERT INTO articles (id, outlet_id, country, url, title, published, full_text, ingested_at, text_source) "
                 "VALUES (?,?,?,?,?,?,?,?,?)", (aid, outlet, "x", f"u/{aid}", aid, ago(hours_ago), "text", ago(hours_ago), text_source))


def test_sources_gate_counts_usable_outlets(conn):
    def health(n_ok):
        for k, o in enumerate(OUTLETS):
            ok = k < n_ok
            store.save_health(conn, FeedHealth(o, ago(0.1 + k / 1e4), int(ok), 1, 10 * ok, 2.0 if ok else None, True, False, True))
    health(len(OUTLETS))
    assert gates.sources(conn, ctx(conn)).ok
    health(len(OUTLETS) // 2)                                     # a newer check: half the feeds broke
    g = gates.sources(conn, ctx(conn))
    assert not g.ok and "usable outlets" in g.reason


def test_ingest_gate_wants_fresh_articles_from_most_outlets(conn):
    for k, o in enumerate(OUTLETS):
        add_article(conn, f"a{k}", o, 1 + k % 5)
    assert gates.ingest(conn, ctx(conn)).ok
    stale = store.connect(":memory:")
    for k, o in enumerate(OUTLETS):
        add_article(stale, f"a{k}", o, 10)                        # nothing newer than 10 h
    g = gates.ingest(stale, ctx(stale))
    assert not g.ok and "newest article" in g.reason
    few = store.connect(":memory:")
    add_article(few, "only", OUTLETS[0], 1)
    assert "active outlets" in gates.ingest(few, ctx(few)).reason


def test_normalize_gate_stops_a_mass_merge(conn):
    c = ctx(conn, before={"canonical_articles": 1000})
    c["after"] = {"canonical_articles": 990}
    assert gates.normalize(conn, c).ok
    c["after"] = {"canonical_articles": 900}
    assert not gates.normalize(conn, c).ok


def test_embed_gate_wants_the_story_window_covered(conn, monkeypatch):
    monkeypatch.setattr("pipeline.embed.run.MODEL", "m")
    for k in range(50):
        add_article(conn, f"a{k}", OUTLETS[k % 5], 24)
    store.save_embeddings(conn, "m", [f"a{k}" for k in range(50)], np.ones((50, 3), dtype=np.float32))
    assert gates.embed(conn, ctx(conn)).ok
    conn.execute("DELETE FROM embeddings WHERE article_id IN ('a1', 'a2')")   # 96 % covered
    g = gates.embed(conn, ctx(conn))
    assert not g.ok and g.metrics["coverage"] == 0.96


def test_stories_gate_wants_a_fresh_run_without_chaining(conn):
    def run(name, members):
        store.save_events(conn, name, members)
    for k in range(240):
        add_article(conn, f"a{k}", OUTLETS[k % 2], 5)
    started = ago(0.5)
    run(f"{ago(0.2)}_average_0.56", {f"a{k}": f"ev{k // 2}" for k in range(240)})      # 120 two-outlet stories
    assert gates.stories(conn, ctx(conn, started_at=started)).ok
    run(f"{ago(0.1)}_average_0.56", {f"a{k}": "ev0" for k in range(240)})               # one giant story
    g = gates.stories(conn, ctx(conn, started_at=started))
    assert not g.ok and "chaining" in g.reason and "≥ 2 outlets" in g.reason
    assert "no new story run" in gates.stories(conn, ctx(conn, started_at=ago(-1))).reason


def test_measure_gate_wants_every_paid_call_logged_and_the_budget_kept(conn):
    started = ago(0.5)
    for k in range(3):
        conn.execute("INSERT INTO judge_calls (call_id, at, purpose, model, prompt_version, direction) "
                     "VALUES (?,?,?,?,?,?)", (f"c{k}", ago(0.1), "measure", "gpt", "v4", "positive"))
        conn.execute("INSERT INTO judge_call_log (call_id, status, started_at) VALUES (?, 'ok', ?)", (f"c{k}", ago(0.1)))
    conn.execute("INSERT INTO positions VALUES (?, 'v4', 'o', 'econ', 0, 1, 1)", (ago(0.05),))
    assert gates.measure(conn, ctx(conn, started_at=started, delta={"spend_usd": 0.1}, budget=0.15)).ok
    over = gates.measure(conn, ctx(conn, started_at=started, delta={"spend_usd": 0.3}, budget=0.15))
    assert not over.ok and "budget" in over.reason
    conn.execute("DELETE FROM judge_call_log WHERE call_id = 'c0'")
    unlogged = gates.measure(conn, ctx(conn, started_at=started, delta={"spend_usd": 0.1}, budget=0.15))
    assert not unlogged.ok and "without a log row" in unlogged.reason
