"""A82/A83 site frames: one frame per week and the latest day; countries and outlets on one
scale around a fixed point; an outlet is drawn past the threshold, a country past it summed over its outlets, each from
the frame its comparisons reach it; the files decode to what was written."""
import json
import sqlite3

import numpy as np

from pipeline.export import frames as F


def test_one_frame_per_week_and_the_latest_day():
    days = [f"2020-01-{d:02d}" for d in range(1, 16)]
    assert F.pick_days(days) == ["2020-01-07", "2020-01-14", "2020-01-15"]
    assert F.pick_days(["2020-01-09", "2020-01-10"], first="2020-01-08") == ["2020-01-10"]


def test_encode_places_countries_and_outlets_on_one_scale_around_a_fixed_point():
    ents = [{"id": "A", "kind": "c"}, {"id": "B", "kind": "c"}, {"id": "o1", "kind": "o", "country": "A"},
            {"id": "o2", "kind": "o", "country": "B"}]
    d = "2020-01-01"
    cpos = {(d, "econ", "A"): (1.0, 0.1), (d, "social", "A"): (2.0, 0.2),
            (d, "econ", "B"): (3.0, 0.1), (d, "social", "B"): (0.0, 0.2)}
    pos = {(d, "econ", "o1"): (1.5, 0.3), (d, "social", "o1"): (2.0, 0.4)}       # o2 has no position: missing
    f = F.decode(F.encode([d], ents, pos, cpos, ref={None: (2.0, 1.0)}).astype("<i2").tobytes(), 4)[0]
    assert np.allclose(f[0], [-1.0, 1.0, 0.1, 0.2], atol=F.SCALE) and np.allclose(f[1], [1.0, -1.0, 0.1, 0.2], atol=F.SCALE)
    assert np.allclose(f[2], [-0.5, 1.0, 0.3, 0.4], atol=F.SCALE) and np.isnan(f[3]).all()   # the same point, not its country's


def test_history_writes_year_files_a_manifest_and_only_shown_outlets(tmp_path):
    db = tmp_path / "h.sqlite"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE history_positions (day TEXT, axis TEXT, outlet TEXT, mean REAL, sd REAL)")
    c.execute("CREATE TABLE history_country_positions (day TEXT, axis TEXT, country TEXT, mean REAL, sd REAL)")
    days = ["2019-12-25", "2019-12-31", "2020-01-02", "2020-01-08"]
    for d in days:
        for ax in ("econ", "social"):
            c.executemany("INSERT INTO history_positions VALUES (?,?,?,?,?)",
                          [(d, ax, "nrk", 0.2, 0.1), (d, ax, "vg", -0.2, 0.1), (d, ax, "aftenposten", 0.0, 0.5)])
            c.execute("INSERT INTO history_country_positions VALUES (?,?,?,?,?)", (d, ax, "Norway", 0.1, 0.05))
    c.execute("CREATE TABLE articles (id TEXT, url TEXT)")
    c.execute("CREATE TABLE judge_calls (purpose TEXT, first_outlet TEXT, second_outlet TEXT, event_id TEXT, "
              "first_article TEXT, second_article TEXT)")
    calls = [("nrk", "aftenposten", "2019-12-28")] * 3000 + [("nrk", "vg", "2020-01-05")] * 2000 + \
            [("vg", "aftenposten", "2020-01-07")] * 3000          # Norway 6,000 by 12-28; nrk and vg 5,000 by 01-07
    c.executemany("INSERT INTO judge_calls VALUES ('history', ?, ?, ?, 'a', 'b')", [(a, b, f"{d}:e") for a, b, d in calls])
    c.commit()
    prior = tmp_path / "prior.json"
    prior.write_text(json.dumps({"as_of": days[-1], "first": days[0],
                                 "outlets": {"nrk": {"n": 9000}, "vg": {"n": 6000}, "aftenposten": {"n": 12}}}))
    out = tmp_path / "site"
    man = F.history(db, out, threshold=5000, prior=prior)
    ids = [e["id"] for e in man["entities"]]
    assert ids == ["Norway", "nrk", "vg"] and [r["id"] for r in man["inconclusive"] if r["id"] == "aftenposten"]
    assert [ch["days"] for ch in man["chunks"]] == [["2019-12-31"], ["2020-01-02", "2020-01-08"]]
    assert [e["since"] for e in man["entities"]] == ["2019-12-31", "2020-01-08", "2020-01-08"]
    f = np.concatenate([F.decode((out / ch["file"]).read_bytes(), len(ids)) for ch in man["chunks"]])
    assert np.allclose(f[:, 0, :2], 0.1, atol=F.SCALE)                 # the country from its outlets' sum, frame one
    assert np.isnan(f[:2, 1:]).all() and np.allclose(f[2, 1, 0], 0.2, atol=F.SCALE)   # its outlets from their own
    again = F.history(db, out, threshold=5000, prior=prior)                 # same content, same file names
    assert [ch["file"] for ch in again["chunks"]] == [ch["file"] for ch in man["chunks"]]
    assert "3 frames × 3 entities" in F.check(out)
