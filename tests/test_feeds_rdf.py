"""RSS 1.0 (RDF) feeds, e.g. ORF's, were read as zero items: the items sit beside <channel>, in the RSS 1.0 namespace."""
from pipeline.ingest.feeds import parse_feed, to_utc_iso

RDF = b"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:dc="http://purl.org/dc/elements/1.1/"
         xmlns="http://purl.org/rss/1.0/">
  <channel rdf:about="https://orf.at/"><title>news.ORF.at</title><link>https://orf.at/</link></channel>
  <item rdf:about="https://orf.at/stories/1/"><title>Erste Meldung</title><link>https://orf.at/stories/1/</link>
    <dc:date>2026-10-14T18:02:00+02:00</dc:date></item>
  <item rdf:about="https://orf.at/stories/2/"><title>Zweite Meldung</title><link>https://orf.at/stories/2/</link>
    <dc:date>2026-10-14T17:40:00+02:00</dc:date><description>Kurz.</description></item>
</rdf:RDF>"""


def test_rdf_items_are_read():
    items = parse_feed(RDF)
    assert [i["title"] for i in items] == ["Erste Meldung", "Zweite Meldung"]
    assert items[0]["url"] == "https://orf.at/stories/1/"
    assert to_utc_iso(items[0]["published"]) == "2026-10-14T16:02:00Z"
    assert items[1]["summary"] == "Kurz."
