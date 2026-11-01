"""A82/A83 site frames: one frame per week and the latest day; countries relative to the shown countries' mean, outlets
relative to their country; only outlets past the threshold are drawn; the files decode to what was written."""
import json
import sqlite3

import numpy as np

from pipeline.export import frames as F


def test_one_frame_per_week_and_the_latest_day():
    days = [f"2020-01-{d:02d}" for d in range(1, 16)]
    assert F.pick_days(days) == ["2020-01-07", "2020-01-14", "2020-01-15"]
    assert F.pick_days(["2020-01-09", "2020-01-10"], first="2020-01-08") == ["2020-01-10"]


def test_encode_centres_countries_on_their_mean_and_outlets_on_their_country():
    ents = [{"id": "A", "kind": "c"}, {"id": "B", "kind": "c"}, {"id": "o1", "kind": "o", "country": "A"},
            {"id": "o2", "kind": "o", "country": "B"}]
    d = "2020-01-01"
    cpos = {(d, "econ", "A"): (1.0, 0.1), (d, "social", "A"): (2.0, 0.2),
            (d, "econ", "B"): (3.0, 0.1), (d, "social", "B"): (0.0, 0.2)}
    pos = {(d, "econ", "o1"): (1.5, 0.3), (d, "social", "o1"): (2.0, 0.4)}       # o2 has no position: missing
    f = F.decode(F.encode([d], ents, pos, cpos).astype("<i2").tobytes(), 4)[0]
    assert np.allclose(f[0], [-1.0, 1.0, 0.1, 0.2], atol=F.SCALE) and np.allclose(f[1], [1.0, -1.0, 0.1, 0.2], atol=F.SCALE)
    assert np.allclose(f[2], [0.5, 0.0, 0.3, 0.4], atol=F.SCALE) and np.isnan(f[3]).all()


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
    c.commit()
    prior = tmp_path / "prior.json"
    prior.write_text(json.dumps({"as_of": days[-1], "first": days[0],
                                 "outlets": {"nrk": {"n": 9000}, "vg": {"n": 6000}, "aftenposten": {"n": 12}}}))
    out = tmp_path / "site"
    man = F.history(db, out, threshold=5000, prior=prior)
    ids = [e["id"] for e in man["entities"]]
    assert ids == ["Norway", "nrk", "vg"] and [r["id"] for r in man["inconclusive"] if r["id"] == "aftenposten"]
    assert [ch["days"] for ch in man["chunks"]] == [["2019-12-31"], ["2020-01-02", "2020-01-08"]]
    f = np.concatenate([F.decode((out / ch["file"]).read_bytes(), len(ids)) for ch in man["chunks"]])
    assert np.allclose(f[:, 1, 0], 0.1, atol=F.SCALE) and np.allclose(f[:, 0, :2], 0, atol=F.SCALE)
    again = F.history(db, out, threshold=5000, prior=prior)                 # same content, same file names
    assert [ch["file"] for ch in again["chunks"]] == [ch["file"] for ch in man["chunks"]]
    assert "3 frames × 3 entities" in F.check(out)
