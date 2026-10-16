"""A63: ingest looks up only the URLs in an outlet's feeds; same new articles and headline edits as loading them all."""
from pipeline import store
from pipeline.contracts import Outlet
from pipeline.ingest import run as ingest


def test_lookup_finds_only_the_asked_urls_with_their_canonical_and_headlines(tmp_path):
    db = tmp_path / "t.sqlite"
    conn = store.connect(db)
    conn.execute("INSERT INTO articles (id, outlet_id, country, url, title, published) VALUES "
                 "('c1','a','SE','https://x/1','First','2026-10-10T00:00:00Z'),"
                 "('c2','a','SE','https://x/2','Copy','2026-10-10T00:00:00Z')")
    conn.execute("UPDATE articles SET canonical_id = 'c1' WHERE id = 'c2'")
    store.seed_headlines(conn)
    conn.commit()
    got = ingest.known_urls(["https://x/1", "https://x/2", "https://x/9"], db)
    assert got == {"https://x/1": ("c1", {"first"}), "https://x/2": ("c1", {"first"})}   # a copy answers as its canonical


def test_ingest_outlet_new_edit_and_unchanged(monkeypatch):
    items = [{"url": "https://x/1", "title": "First"}, {"url": "https://x/2", "title": "Second, edited"},
             {"url": "https://x/3", "title": "Brand new", "summary": "s"}, {"url": "https://x/3", "title": "Brand new"}]
    monkeypatch.setattr(ingest.net, "fetch", lambda url: (b"", None))
    monkeypatch.setattr(ingest, "parse_feed", lambda body: items)
    monkeypatch.setattr(ingest, "fetch_text", lambda url: (None, "none"))
    asked = []

    def lookup(urls):
        asked.extend(urls)
        return {"https://x/1": ("c1", {"first"}), "https://x/2": ("c2", {"second"})}
    o = Outlet(id="a", name="A", country="SE", language="sv", homepage="https://x", feeds=("https://x/feed",))
    new, errors, edits = ingest.ingest_outlet(o, True, lookup)
    assert sorted(set(asked)) == ["https://x/1", "https://x/2", "https://x/3"]
    assert [a.url for a in new] == ["https://x/3"] and not errors
    assert [(e.article_id, e.title) for e in edits] == [("c2", "Second, edited")]
