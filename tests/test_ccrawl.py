import datetime as dt

from pipeline.history import ccrawl


def test_an_address_matches_its_index_form():
    assert ccrawl.norm("https://www.adevarul.ro/news/x-1_5da7/") == ccrawl.norm("http://adevarul.ro/news/x-1_5da7/index.html")
    assert ccrawl.norm("https://e.com/a?b=2&a=1#top") == "e.com/a?a=1&b=2"
    assert ccrawl.norm("https://e.com") == "e.com/"


def test_host_key_is_the_surt_host_without_www():
    assert ccrawl.host_key("www.example.co.uk") == "uk,co,example"
    assert ccrawl.host_key("WWW2.Example.com:443") == "com,example"


def test_only_crawls_after_the_sighting_nearest_first(monkeypatch):
    monkeypatch.setattr(ccrawl, "_crawls", [(dt.date(2019, 9, 16), "CC-MAIN-2019-38"), (dt.date(2019, 10, 14), "CC-MAIN-2019-42"),
                                            (dt.date(2019, 11, 11), "CC-MAIN-2019-46"), (dt.date(2021, 1, 4), "CC-MAIN-2021-01")])
    assert ccrawl.after(dt.datetime(2019, 10, 1)) == ["CC-MAIN-2019-42", "CC-MAIN-2019-46"]
    assert ccrawl.after(dt.datetime(2019, 10, 1), n=1) == ["CC-MAIN-2019-42"]
