"""H5 (pipeline.history.trajectories): the site's lines are the replay's own day-by-day positions (A74), read back —
one point per bin (its last day), outlets from their first verdict, countries with the sd the replay wrote."""
import datetime as dt

import numpy as np

from pipeline import store
from pipeline.history import positions as HP
from pipeline.history import trajectories as H5
from pipeline.judge import prompt as P

OUTLETS = ["a1", "a2", "b1", "c1"]
GROUPS = {"A": {"a1": 2.0, "a2": 1.0}, "B": {"b1": 1.0}, "C": {"c1": 1.0}}
START = dt.date(2019, 1, 1)


def db(tmp_path):
    """14 days of positions; a1 and b1 judged from day 0, a2 from day 9, c1 never."""
    conn = store.connect(tmp_path / "h.sqlite")
    states = {ax: HP.start(OUTLETS, ax, 1.0, 0.5) for ax in P.AXES}
    for d in range(14):
        day = (START + dt.timedelta(days=d)).isoformat()
        rows = [("a1", "b1", f"x{d}", f"y{d}", day, day, 1.0, d % 3 != 0)]
        if d >= 9:
            rows.append(("a2", "b1", f"z{d}", f"y{d}", day, day, 1.0, True))
        for k, r in enumerate(rows):
            for art, o in ((r[2], r[0]), (r[3], r[1])):
                conn.execute("INSERT OR IGNORE INTO articles (id, outlet_id, country, url, title, published, summary) "
                             "VALUES (?,?,?,?,?,?,?)", (art, o, "x", f"https://x/{art}", "t", f"{day}T12:00:00Z", "s"))
            conn.execute("INSERT INTO judge_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (f"c{d}_{k}", "2026-10-19T00:00:00Z", "history", "judge", P.PROMPT_VERSION, "positive",
                          f"ev{d}:summary", r[2], r[3], r[0], r[1], "summary", 100, 10, 0.0, ""))
            for ax in P.AXES:
                v = "A" if r[7] else "B"
                conn.execute("INSERT INTO judgments VALUES (?,?,?,?,?)", (f"c{d}_{k}", ax, v, P.first_wins(v, "positive"), None))
        for st in states.values():
            HP.update(st, rows)
        HP.record_day(conn, day, states, GROUPS)
    conn.commit()
    return conn, states


def test_lines_are_the_stored_positions_one_per_week(tmp_path):
    conn, states = db(tmp_path)
    out = H5.build(conn, GROUPS, bin_days=7)
    econ = out["axes"]["econ"]
    assert out["start"] == START.isoformat() and set(econ["outlets"]) == {"a1", "a2", "b1"}      # c1: never judged
    assert [p[0] for p in econ["outlets"]["a1"]] == [0, 1] and [p[0] for p in econ["outlets"]["a2"]] == [1]
    k = OUTLETS.index("a1")                         # the last bin's point is the last day's stored position
    assert np.isclose(econ["outlets"]["a1"][-1][1], states["econ"].mean[k], atol=1e-4)
    assert np.isclose(econ["outlets"]["a1"][-1][2], np.sqrt(states["econ"].cov[k, k]), atol=1e-4)


def test_countries_carry_the_sd_of_their_weighted_mean(tmp_path):
    conn, states = db(tmp_path)
    s = states["social"]
    w = np.array([2 / 3, 1 / 3])
    m, sd = w @ s.mean[:2], np.sqrt(w @ s.cov[:2, :2] @ w)
    social = H5.build(conn, GROUPS)["axes"]["social"]["countries"]
    assert np.isclose(social["A"][-1][1], m, atol=1e-4) and np.isclose(social["A"][-1][2], sd, atol=1e-4)
    assert "C" not in social                                             # no outlet of C judged yet
