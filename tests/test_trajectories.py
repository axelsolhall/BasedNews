"""H5 (pipeline.history.trajectories): history verdicts in the database → static fit (σ_a, τ, β) → dynamic fit →
outlet and country lines relative to the week's reference average, for the site. A planted move must come out."""
import datetime as dt
import json

import numpy as np
import pytest

from pipeline import store
from pipeline.history import trajectories as H5
from pipeline.judge import prompt as P
from tests.test_dynamic import simulate

START = dt.date(2016, 1, 4)


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    outlets, truth, comps, wins = simulate(4, n=7, T=30, step=(0, 15, 1.5), active={6: range(0, 12)})
    conn = store.connect(tmp_path_factory.mktemp("h") / "h.sqlite")
    placed = {}
    for k, (oa, ob, a, b, d, ta, tb) in enumerate(comps):
        for art, o, t in ((a, oa, ta), (b, ob, tb)):
            if art not in placed:
                day = START + dt.timedelta(days=7 * t + (hash(art) % 7))
                conn.execute("INSERT INTO articles (id, outlet_id, country, url, title, published, summary) VALUES "
                             "(?,?,?,?,?,?,?)", (art, o, "x", f"https://x/{art}", "t", f"{day.isoformat()}T12:00:00Z", "s"))
                placed[art] = t
        conn.execute("INSERT INTO judge_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (f"c{k}", "2026-10-18T00:00:00Z", "history", "judge", P.PROMPT_VERSION, "positive", f"ev{k}:full",
                      a, b, oa, ob, "full", 100, 10, 0.0, ""))
        for ax in P.AXES:
            v = "A" if wins[k] else "B"
            conn.execute("INSERT INTO judgments VALUES (?,?,?,?,?)", (f"c{k}", ax, v, P.first_wins(v, "positive"), None))
    conn.commit()
    return conn, outlets, truth


def test_lines_come_out_relative_with_the_planted_move(db):
    conn, outlets, truth = db
    rows = H5.verdicts(conn)
    assert len(rows) == 2 * conn.execute("SELECT count(*) FROM judge_calls").fetchone()[0]
    posts = H5.build(rows, outlets, START)
    groups = {"Here": {f"o{k}": 1.0 for k in range(1, 6)}}
    out = json.loads(json.dumps(H5.to_json(posts, groups, START)))          # JSON-clean
    line = {w: (m, s) for w, m, s in out["axes"]["econ"]["outlets"]["o0"]}
    assert line[25][0] - line[5][0] > 0.9                       # planted +1.5 at week 15
    assert all(s > 0 for _, s in line.values())
    weeks6 = [w for w, _, _ in out["axes"]["econ"]["outlets"]["o6"]]
    assert max(weeks6) <= 12                                    # an outlet's line stops where its verdicts stop
    here = out["axes"]["econ"]["countries"]["Here"]
    assert all(abs(m) < 1e-6 for _, m, _ in here)               # the reference average is zero by construction
    assert out["axes"]["econ"]["q"] in [float(k) for k in out["axes"]["econ"]["evidence"]]


def test_bins_are_weeks_from_the_start():
    assert H5.bin_of("2016-01-04T00:00:00Z", START) == 0
    assert H5.bin_of("2016-01-10T23:59:00Z", START) == 0
    assert H5.bin_of("2016-01-11T00:00:00Z", START) == 1
    assert np.isclose(H5.bin_of("2017-01-02T08:00:00Z", START), 52)
