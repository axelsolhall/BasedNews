"""GDELT's raw GKG files (A75): a row of a 15-minute file becomes the history's GDELT row, only for the asked outlets."""
import datetime as dt
import io
import zipfile

from pipeline.history import gdelt_raw as R


def _zip(lines):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("x.gkg.csv", "\n".join("\t".join(f) for f in lines) + "\n")
    return buf.getvalue()


def _row(url, when="20190315120000"):
    f = [""] * 27
    f[0], f[1], f[4] = "1", when, url
    f[9] = "1#Berlin, Berlin, Germany#GM#GM16#52.5#13.4#-1746443;4#Paris, France#FR#FR11#48.8#2.3#-1456928"
    f[11], f[13] = "angela merkel;emmanuel macron", "european union"
    return f


def test_a_raw_row_becomes_a_history_row_for_the_asked_outlets_only():
    rules = [("sz", "sueddeutsche.de", ""), ("welt", "welt.de", "")]
    data = _zip([_row("https://www.sueddeutsche.de/politik/a-1"), _row("https://www.spiegel.de/b"),
                 _row("https://www.welt.de/c", "20190315121500")])
    got = R.rows_of(data, {"sueddeutsche.de", "welt.de"}, rules, {"sz": "DE", "welt": "DE"})
    assert [(r["outlet"], r["url"]) for r in got] == [("sz", "https://www.sueddeutsche.de/politik/a-1"),
                                                      ("welt", "https://www.welt.de/c")]
    assert got[0]["seen"] == dt.datetime(2019, 3, 15, 12, 0, tzinfo=dt.timezone.utc)
    assert got[0]["places"] == "Berlin, Berlin, Germany;Paris, France"
    assert got[0]["persons"] == "angela merkel;emmanuel macron" and got[0]["orgs"] == "european union"
    assert R.rows_of(None, {"welt.de"}, rules, {}) == []                   # a quarter-hour GDELT skipped
