"""A63: normalize reads one outlet's last 14 days, plus older copies of their texts; headline history by id only."""
from pipeline import store
from pipeline.normalize.run import shared_text_groups

SINCE = "2026-10-02T00:00:00Z"


def add(conn, id, outlet, published, title, text):
    conn.execute("INSERT INTO articles (id, outlet_id, country, url, title, published, full_text) VALUES (?,?,?,?,?,?,?)",
                 (id, outlet, "SE", f"https://x/{id}", title, published, text))


def test_window_keeps_old_copies_of_new_texts_and_skips_old_only_groups():
    conn = store.connect(":memory:")
    add(conn, "old1", "a", "2026-09-01T10:00:00Z", "First headline", "the same body")
    add(conn, "new1", "a", "2026-10-10T10:00:00Z", "Second headline", "the same body")      # copy of an old text
    add(conn, "old2", "a", "2026-09-01T10:00:00Z", "One", "an old body")
    add(conn, "old3", "a", "2026-09-02T10:00:00Z", "Two", "an old body")                     # resolved long ago
    add(conn, "new2", "b", "2026-10-10T10:00:00Z", "Elsewhere", "the same body")             # other outlet
    groups = shared_text_groups(conn, SINCE, "a")
    assert [sorted(r["id"] for r in g) for g in groups] == [["new1", "old1"]]
    assert shared_text_groups(conn, SINCE, "b") == []


def test_headline_history_by_id_matches_the_full_table():
    conn = store.connect(":memory:")
    for i in range(2000):                                                                    # > one 900-id chunk
        add(conn, f"n{i}", "a", "2026-10-10T10:00:00Z", f"title {i}", None)
    store.seed_headlines(conn)
    conn.execute("INSERT INTO headlines VALUES ('n5', 'title 5 renamed', '2026-10-10T11:00:00Z', 'https://x/n5', 'renamed')")
    full = store.headline_history(conn)
    some = store.headline_history(conn, [f"n{i}" for i in range(0, 2000, 3)] + ["n5", "n5"])
    assert some == {k: v for k, v in full.items() if k in some} and len(some) == 668
    assert [h.title for h in some["n5"]] == ["title 5", "title 5 renamed"]
