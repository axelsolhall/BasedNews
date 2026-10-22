"""The history backfill (A75) without the network: which sitemap names a month, a month's dated entries from a
sitemap tree, and the page-date rule for sitemap finds."""
from pipeline.history import backfill as B
from pipeline.history import sitemaps as SM
from pipeline.history.fetch import Page


def test_a_sitemap_address_names_its_period():
    assert SM.period("https://www.welt.de/sitemaps/sitemap/2019/03/sitemap.xml.gz") == (2019, 3)
    assert SM.period("https://x.se/sitemap-2019-03-14.xml") == (2019, 3)
    assert SM.period("https://x.pl/sitemap.xml?year=2019&month=3") == (2019, 3)
    assert SM.period("https://x.dk/sitemap_2019.xml") == (2019, None)
    assert SM.period("https://x.no/sitemap.xml") == (None, None)


def test_a_months_articles_come_from_the_leaves_that_can_hold_it(tmp_path, monkeypatch):
    monkeypatch.setattr(SM, "CACHE", tmp_path)
    pages = {
        "https://x.no/robots.txt": "User-agent: *\nSitemap: https://x.no/index.xml\n",
        "https://x.no/index.xml": "<sitemapindex><sitemap><loc>https://x.no/sm/2019/03.xml</loc></sitemap>"
                                  "<sitemap><loc>https://x.no/sm/2019/04.xml</loc></sitemap>"
                                  "<sitemap><loc>https://x.no/sm/2014/03.xml</loc></sitemap></sitemapindex>",
        "https://x.no/sm/2019/03.xml": "<urlset><url><loc>https://x.no/a</loc><lastmod>2019-03-02T10:00:00Z</lastmod></url>"
                                       "<url><loc>https://x.no/b</loc></url>"                       # undated: left out
                                       "<url><loc>https://x.no/c</loc><lastmod>2019-04-02</lastmod></url></urlset>",
        "https://x.no/sm/2019/04.xml": "<urlset><url><loc>https://x.no/d</loc><lastmod>2019-04-01</lastmod></url></urlset>",
    }
    read = []
    monkeypatch.setattr(SM, "get", lambda u: read.append(u) or pages.get(u))
    got = SM.month_urls("x", "https://x.no/", 2019, 3)
    assert [u for u, _ in got] == ["https://x.no/a"]
    assert "https://x.no/sm/2019/04.xml" not in [u for u in read if u.endswith("04.xml")][1:]   # listed, not read
    assert not any("2014" in u for u in read)
    assert {leaf["url"] for leaf in SM.tree("x", "https://x.no/")} == {"https://x.no/sm/2019/03.xml",
                                                                         "https://x.no/sm/2019/04.xml"}


def test_a_sitemap_find_counts_only_when_the_page_dates_itself_in_the_month():
    page = lambda d: Page("t", "s", d)                                                       # noqa: E731
    assert B.in_month(page("2019-03-31T23:00:00Z"), 2019, 3)
    assert B.in_month(page("2019-04-02T08:00:00Z"), 2019, 3)                                # within the 3-day margin
    assert not B.in_month(page("2019-05-01T00:00:00Z"), 2019, 3)                            # a later edit's date
    assert not B.in_month(page(None), 2019, 3) and not B.in_month(None, 2019, 3)


def test_months_run_in_order_across_a_year_end():
    assert list(B.months("2018-11", "2019-02")) == [(2018, 11), (2018, 12), (2019, 1), (2019, 2)]


def test_the_archive_reaches_every_outlet_before_it_deepens_any():
    from collections import Counter
    rows = [{"outlet": o, "url": f"{o}{k}"} for o, n in (("big", 3), ("none", 2), ("few", 2)) for k in range(n)]
    got = [r["url"] for r in B.neediest_first(rows, Counter({"big": 500, "few": 10}))]
    assert got == ["none0", "few0", "big0", "none1", "few1", "big1", "big2"]


def test_an_unanswered_archive_request_is_not_recorded_as_tried(monkeypatch):
    import datetime as dt
    import urllib.error
    wb = B.Wayback(workers=1, gap=0)
    monkeypatch.setattr(wb, "allowed", lambda url: True)
    monkeypatch.setattr(wb, "_pause", lambda s, why: None)

    def refuse(*a, **k):
        raise urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))
    monkeypatch.setattr(B.urllib.request, "urlopen", refuse)
    assert wb.get({"url": "https://x.no/a", "seen": dt.datetime(2019, 3, 1)}) is B.RETRY


def test_a_urlset_that_lists_sitemaps_is_an_index_and_undated_leaves_are_read_once(tmp_path, monkeypatch):
    monkeypatch.setattr(SM, "CACHE", tmp_path)
    pages = {
        "https://x.it/robots.txt": "",                                                       # names no sitemap
        "https://x.it/sitemap.xml": "<urlset><url><loc>https://x.it/sitemap/Articolo-2019-03.xml</loc></url>"
                                    "<url><loc>https://x.it/sitemap/arts-1.xml</loc></url></urlset>",
        "https://x.it/sitemap/Articolo-2019-03.xml": "<urlset><url><loc>https://x.it/a</loc>"
                                                     "<lastmod>2019-03-05</lastmod></url></urlset>",
        "https://x.it/sitemap/arts-1.xml": "<urlset><url><loc>https://x.it/b</loc>"
                                           "<lastmod>2019-03-06</lastmod></url></urlset>",
    }
    read = []
    monkeypatch.setattr(SM, "get", lambda u: read.append(u) or pages.get(u))
    assert [u for u, _ in SM.month_urls("x", "https://x.it/", 2019, 3)] == ["https://x.it/a", "https://x.it/b"]
    SM.month_urls("x", "https://x.it/", 2019, 4)
    assert read.count("https://x.it/sitemap/arts-1.xml") == 1


class _Resp:
    def __init__(self, url, body):
        self.url, self.body, self.headers = url, body.encode(), {"Content-Type": "text/html"}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def geturl(self):
        return self.url

    def read(self, n=-1):
        out, self.body = (self.body, b"") if n < 0 else (self.body[:n], self.body[n:])
        return out


def test_a_page_found_late_or_behind_a_same_site_refresh_is_read(monkeypatch):
    from pipeline.history import fetch as F
    late = "<html><head></head><body>" + "x" * 300_000 + '<meta property="og:title" content="Late"/>' \
           '<meta property="article:published_time" content="2019-01-03T18:11:57+02:00"/></body>'
    pages = {"https://a.fi/1": late,
             "https://d.si/old-1.html": '<meta property="og:title" content=""/>'
                                        '<meta http-equiv="refresh" content="0; URL=/new/"/>',
             "https://d.si/new/": '<meta property="og:title" content="New"/>'
                                  '<meta property="article:published_time" content="2019-01-11T18:55:00Z"/>',
             "https://e.si/old": '<meta http-equiv="refresh" content="0; URL=https://elsewhere.com/x"/>'}
    monkeypatch.setattr(F.urllib.request, "urlopen", lambda req, timeout: _Resp(req.full_url, pages[req.full_url]))
    assert F.fetch_page("https://a.fi/1").title == "Late"
    assert F.fetch_page("https://d.si/old-1.html").published == "2019-01-11T18:55:00Z"
    assert F.fetch_page("https://e.si/old") is None                         # another site: not followed


def test_a_wall_or_a_section_page_is_not_stored_as_an_article():
    from pipeline.history import quality as Q
    page = lambda t: Page(t, None, "2019-03-01T10:00:00Z")                                      # noqa: E731
    items = [("u1", "lemonde", page("Client Challenge")), ("u2", "lemonde", page("Brexit : le vote reporté")),
             ("u3", "err-est", page("uudised"))] + [(f"s{k}", "bhrt-bos", page("bhrt")) for k in range(5)]
    assert Q.kept(items) == {"u2", "u3"}                                    # a wall; one title 5 times in a batch
    assert Q.kept(items, junk={("err-est", "uudised")}) == {"u2"}           # a title the outlet gave ≥ REPEAT times


def test_one_article_under_several_addresses_is_stored_once():
    from pipeline.history import quality as Q
    page = lambda t: Page(t, None, "2019-03-01T10:00:00Z")                                      # noqa: E731
    items = [("a1", "cbsnews", page("Doomsday Clock 2019")), ("a2", "cbsnews", page("Doomsday Clock 2019")),
             ("b", "france24", page("Challenge Validation")), ("c", "telegraph", page("Loading...")),
             ("d", "npr", page("Doomsday Clock 2019"))]
    assert Q.kept(items) == {"a1", "d"}


def test_pages_without_an_article_do_not_make_a_site_given_up(monkeypatch):
    import urllib.error

    from pipeline.history import fetch as F
    calls = []

    def fake(req, timeout):
        calls.append(req.full_url)
        if "/gone" in req.full_url:
            raise urllib.error.HTTPError(req.full_url, 404, "gone", {}, None)
        if "/busy" in req.full_url:
            raise urllib.error.HTTPError(req.full_url, 503, "busy", {}, None)
        return _Resp(req.full_url, '<meta property="og:title" content="A"/>')
    monkeypatch.setattr(F.urllib.request, "urlopen", fake)
    monkeypatch.setattr(F.net, "robots_allows", lambda u: True)
    monkeypatch.setattr(F._Polite, "PAUSE", 0.0)
    urls = [f"https://dr.dk/gone{k}" for k in range(5)] + ["https://dr.dk/ok"]
    got = F.fetch_many(urls, workers=1, gap=0, give_up=3, skipped=F.SKIPPED)
    assert got["https://dr.dk/ok"].title == "A"                             # five 404s in a row: still asked
    got = F.fetch_many([f"https://x.dk/busy{k}" for k in range(5)], workers=1, gap=0, give_up=3, skipped=F.SKIPPED)
    assert sum(p is F.SKIPPED for p in got.values()) == 2                   # three 503s: the rest skipped


def test_a_site_gets_more_requests_in_flight_until_it_says_slow_down(monkeypatch):
    import urllib.error

    from pipeline.history import fetch as F
    monkeypatch.setattr(F.net, "robots_allows", lambda u: True)
    monkeypatch.setattr(F._Polite, "PAUSE", 0.0)

    def fake(req, timeout):
        if "/slow" in req.full_url:
            raise urllib.error.HTTPError(req.full_url, 429, "slow down", {}, None)
        return _Resp(req.full_url, '<meta property="og:title" content="A"/>')
    monkeypatch.setattr(F.urllib.request, "urlopen", fake)
    p = F._Polite(gap=0, give_up=99, start=2, most=8)
    for k in range(F._Polite.RAISE_AFTER * 10):
        p.get(f"https://a.se/{k}")
    assert p.sites["a.se"].c == 8                                        # +1 per 20 good answers, at most 8
    p.get("https://a.se/slow")
    assert p.sites["a.se"].c == 4                                        # halved when told to slow down
    for _ in range(3):
        p.get("https://a.se/slow")
    assert p.sites["a.se"].c == 1                                        # never below one
