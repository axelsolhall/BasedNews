"""A58: pairs are judged as they appear (no settling by default); `settle_hours` can hold back young articles, and an
ongoing story is then judged in slices. Temporary database, no network, no spend."""
import datetime as dt

from pipeline import store
from pipeline.measure.run import SETTLE_HOURS, candidates
from pipeline.settings import Settings

NOW = dt.datetime(2026, 10, 12, 12, 0, tzinfo=dt.UTC)
REL = {"econ": 0.5, "social": 0.8}


def at(hours_ago):
    return (NOW - dt.timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def pairs(cands):
    return {(c.event_id.split(":")[0], frozenset((c.article_a, c.article_b))) for c in cands}


def test_pairs_appear_as_articles_arrive_and_the_settle_dial_holds_back_young_ones():
    conn = store.connect(":memory:")
    conn.execute("CREATE TABLE same_text_pairs (run_id TEXT, a TEXT, b TEXT, jaccard REAL)")
    stories = {"closed": {"nrk": 130, "svt": 110}, "ongoing": {"nrk": 140, "svt": 100, "dr": 10}}
    for ev, ages in stories.items():
        for outlet, age in ages.items():
            aid = f"{ev}-{outlet}"
            conn.execute("INSERT INTO articles (id, outlet_id, country, url, title, published, full_text, ingested_at) "
                         "VALUES (?,?,?,?,?,?,?,?)", (aid, outlet, "x", f"u/{aid}", f"T {aid}", at(age), "text " * 100, at(age)))
            store.save_events(conn, "run1", {aid: ev})
    assert SETTLE_HOURS == 0
    now_pairs, ctx = candidates(conn, Settings(), REL, now=NOW)
    assert len(pairs(now_pairs)) == 1 + 3 and ctx["settled"] == ctx["articles"] == 5          # everything, as it appears
    held, ctx = candidates(conn, Settings(), REL, settle_hours=96, now=NOW)
    assert ctx["settled"] == 4 and pairs(held) == {("closed", frozenset({"closed-nrk", "closed-svt"})),
                                                   ("ongoing", frozenset({"ongoing-nrk", "ongoing-svt"}))}
